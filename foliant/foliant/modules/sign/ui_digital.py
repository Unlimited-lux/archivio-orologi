"""Dialoghi della firma digitale: firma/certifica, campo firma, ID autofirmato,
gestione ID e certificati attendibili, ripetizione della firma grafica."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from foliant.api import _, theme

from . import graphic as g

P12_FILTER = "ID digitale (*.p12 *.pfx)"
CERT_FILTER = "Certificati (*.cer *.crt *.pem *.der *.p7b *.p7c)"
REASONS = ["Approvo il documento", "Sono l'autore del documento", "Ho revisionato il documento",
           "Accetto i termini indicati"]
LEVELS = [(1, "Nessuna modifica consentita"), (2, "Consentite solo compilazione dei moduli e firme"),
          (3, "Consentite compilazione dei moduli, firme e annotazioni")]


def _date(dt) -> str:
    return dt.strftime("%d/%m/%Y") if dt else "—"


def _error_label() -> QLabel:
    lab = QLabel("")
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('danger')}")
    return lab


# ============================================================ firma digitale
class DigitalSignDialog(QDialog):
    """Scelta dell'ID digitale, password, motivo/luogo/contatto, aspetto e marca
    temporale. Alla conferma `digital_id` e `options` sono pronti per sign_pdf."""

    def __init__(self, parent, controller, *, certify: bool = False, visible: bool = True, field=None):
        super().__init__(parent)
        from . import digital, store
        self.c = controller
        self.digital = digital
        self.store = store
        self.certify = certify
        self.visible = visible
        self.field = field
        self.digital_id = None
        self.options = None
        self._failures = 0
        self.setWindowTitle(_("Certifica il documento") if certify else _("Firma digitale"))
        self.resize(560, 0)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        row = QHBoxLayout()
        self.id_combo = QComboBox()
        self.id_combo.setAccessibleName(_("ID digitale"))
        row.addWidget(self.id_combo, 1)
        self.browse = QPushButton(_("Sfoglia…"))
        row.addWidget(self.browse)
        self.create_id = QPushButton(_("Crea…"))
        self.create_id.setToolTip(_("Crea un ID digitale autofirmato"))
        row.addWidget(self.create_id)
        form.addRow(_("ID digitale:"), row)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName(_("Password dell'ID digitale"))
        form.addRow(_("Password:"), self.password)
        self.cert_info = QLabel(_("Inserisci la password per vedere i dati del certificato."))
        self.cert_info.setWordWrap(True)
        self.cert_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("", self.cert_info)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems([_(r) for r in REASONS])
        constraints = getattr(field, "allowed_reasons", None)
        if constraints:
            self.reason.clear()
            self.reason.addItems(constraints)
            self.reason.setEditable(False)
        form.addRow(_("Motivo:"), self.reason)
        self.location = QLineEdit(str(controller.ctx.settings().value("sign/location", "") or ""))
        form.addRow(_("Luogo:"), self.location)
        self.contact = QLineEdit(str(controller.ctx.settings().value("sign/contact", "") or ""))
        form.addRow(_("Contatto:"), self.contact)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem(_("Personalizzato"), None)
        self.layout_combo = QComboBox()
        for key, label in [("grafica_testo", _("Firma grafica e testo")), ("testo", _("Solo testo")),
                           ("nome_grande", _("Nome grande e dettagli")), ("grafica", _("Solo firma grafica"))]:
            self.layout_combo.addItem(label, key)
        self.graphic_combo = QComboBox()
        self.graphic_combo.addItem(_("Nessuna"), None)
        lib = controller.library(interactive=False)
        if lib is not None and not lib.locked:
            for e in lib.entries("firma"):
                if e.available:
                    self.graphic_combo.addItem(e.name, e.id)
                    if e.default:
                        self.graphic_combo.setCurrentIndex(self.graphic_combo.count() - 1)
        self.show_checks: dict[str, QCheckBox] = {}
        show_row = QHBoxLayout()
        for key, label in [("nome", _("Nome")), ("data", _("Data")), ("motivo", _("Motivo")),
                           ("luogo", _("Luogo")), ("dn", _("Nome distinto")), ("etichette", _("Etichette"))]:
            cb = QCheckBox(label)
            cb.setChecked(key != "dn")
            self.show_checks[key] = cb
            show_row.addWidget(cb)
        self.date_combo = QComboBox()
        for key in digital.DATE_FORMATS:
            self.date_combo.addItem(key, key)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Italiano", "it")
        self.lang_combo.addItem("English", "en")
        self.preview = AppearancePreview(self)
        self.save_profile = QPushButton(_("Salva come aspetto…"))
        for p in store.AppearanceProfiles().list():
            missing = p.graphic and self.graphic_combo.findData(p.graphic) < 0
            self.profile_combo.addItem(p.name + (_(" (da completare)") if missing else ""), p)
            if p.default:
                self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)
        self.profile_combo.currentIndexChanged.connect(lambda _i: (self._apply_profile(), self.update_preview()))
        self.save_profile.clicked.connect(self._save_profile)
        self._apply_profile()
        if visible:
            prow = QHBoxLayout()
            prow.addWidget(self.profile_combo, 1)
            prow.addWidget(self.save_profile)
            form.addRow(_("Profilo aspetto:"), prow)
            form.addRow(_("Disposizione:"), self.layout_combo)
            form.addRow(_("Firma grafica:"), self.graphic_combo)
            form.addRow(_("Mostra:"), show_row)
            drow = QHBoxLayout()
            drow.addWidget(self.date_combo, 1)
            drow.addWidget(QLabel(_("Etichette in:")))
            drow.addWidget(self.lang_combo)
            form.addRow(_("Formato data:"), drow)
            form.addRow(_("Anteprima:"), self.preview)
        else:
            for w in (self.profile_combo, self.save_profile, self.layout_combo, self.graphic_combo,
                      self.date_combo, self.lang_combo, self.preview, *self.show_checks.values()):
                w.hide()
        for sig in (self.layout_combo.currentIndexChanged, self.graphic_combo.currentIndexChanged,
                    self.date_combo.currentIndexChanged, self.lang_combo.currentIndexChanged,
                    self.reason.currentTextChanged, self.location.textChanged):
            sig.connect(lambda *_a: self.update_preview())
        for cb in self.show_checks.values():
            cb.toggled.connect(lambda _v: self.update_preview())
        self.tsa = QComboBox()
        self.tsa.addItem(_("Nessuna marca temporale"), None)
        cfg = store.TSAConfig()
        for s in cfg.list():
            self.tsa.addItem(f"{s.name} — {s.url}", (s.url, s.timeout, s.user))
            if s.default:
                self.tsa.setCurrentIndex(self.tsa.count() - 1)
        req_url = getattr(field, "timestamp_required_url", None)
        if req_url:
            self.tsa.clear()
            self.tsa.addItem(req_url, (req_url, 15.0))
            self.tsa.setToolTip(_("Il campo richiede una marca temporale da questo server."))
        form.addRow(_("Marca temporale:"), self.tsa)
        self.level = QComboBox()
        for lvl, label in LEVELS:
            self.level.addItem(_(label), lvl)
        self.level.setCurrentIndex(1)
        if certify:
            form.addRow(_("Modifiche consentite:"), self.level)
        else:
            self.level.hide()
        self.lock = QLineEdit()
        self.lock.setPlaceholderText(_("nomi dei campi separati da virgola"))
        self.lock.setToolTip(_("Questi campi del modulo non potranno più essere modificati senza invalidare la "
                               "firma."))
        form.addRow(_("Blocca campi dopo la firma:"), self.lock)
        lay.addLayout(form)
        self.allow_expired = QCheckBox(_("Firma comunque con un certificato scaduto (solo per prove: la firma "
                                         "risulterà non valida)"))
        self.allow_expired.hide()
        lay.addWidget(self.allow_expired)
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox()
        self.ok = box.addButton(_("Certifica e salva…") if certify else _("Firma e salva…"),
                                QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._paths: dict[int, str] = {}
        self._reload_ids()
        self.update_preview()
        self.browse.clicked.connect(self._browse)
        self.create_id.clicked.connect(self._create_id)
        self.password.editingFinished.connect(self._preview)

    def allowed_issuers(self) -> set[str] | None:
        issuers = getattr(self.field, "allowed_issuers", None)
        return {c.subject_dn for c in issuers} if issuers else None

    def _reload_ids(self, select_path: str | None = None) -> None:
        self.id_combo.clear()
        allowed = self.allowed_issuers()
        for e in self.store.IDRegistry().list():
            refused = allowed is not None and not self._issuer_allowed(e)
            label = e.alias + (_(" (file non trovato)") if e.missing else "") + \
                (_(" (non ammesso dal campo)") if refused else "")
            self.id_combo.addItem(label, e.path)
            if e.missing or refused:
                self.id_combo.model().item(self.id_combo.count() - 1).setEnabled(False)
            elif (select_path and e.path == select_path) or (not select_path and e.default):
                self.id_combo.setCurrentIndex(self.id_combo.count() - 1)
        if allowed:
            self.cert_info.setText(_("Questo campo accetta solo certificati emessi da: {who}").format(
                who="; ".join(sorted(c.subject for c in self.field.allowed_issuers))))
        if self.id_combo.count() == 0 and not select_path:
            self.id_combo.setPlaceholderText(_("Nessun ID digitale: usa «Sfoglia…» o «Crea…»"))
        if select_path and self.id_combo.findData(select_path) < 0:
            self.id_combo.addItem(Path(select_path).name, select_path)
            self.id_combo.setCurrentIndex(self.id_combo.count() - 1)

    def _issuer_allowed(self, entry_or_info) -> bool:
        issuers = getattr(self.field, "allowed_issuers", None)
        if not issuers:
            return True
        names = {c.subject for c in issuers} | {c.subject_dn for c in issuers}
        fps = {c.sha256 for c in issuers}
        issuer = getattr(entry_or_info, "issuer_dn", None) or getattr(entry_or_info, "issuer", "")
        fp = getattr(entry_or_info, "fingerprint", None) or getattr(entry_or_info, "sha256", "")
        return issuer in names or getattr(entry_or_info, "issuer", "") in names or fp in fps

    def _create_id(self) -> None:
        dlg = SelfSignedDialog(self, self.c)
        if dlg.exec() and dlg.created_path:
            self._reload_ids(dlg.created_path)

    def _browse(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Scegli l'ID digitale"), "", _(P12_FILTER))
        if path:
            self._reload_ids(path)

    def current_path(self) -> str | None:
        return self.id_combo.currentData()

    def _load(self):
        path = self.current_path()
        if not path:
            raise self.digital.SignError(_("Scegli un ID digitale (file .p12 o .pfx)."))
        return self.digital.load_pkcs12(path, self.password.text())

    def _preview(self) -> None:
        if not self.password.text() or not self.current_path():
            return
        try:
            did = self._load()
        except self.digital.SignError as e:
            self.cert_info.setText(str(e))
            return
        self._show_info(did)

    def _show_info(self, did) -> None:
        i = did.info
        self._preview_info = i
        self.update_preview()
        txt = _("Titolare: {s}\nEmittente: {iss}\nValido dal {a} al {b}\nUsi della chiave: {ku}").format(
            s=i.subject, iss=i.issuer, a=_date(i.not_before), b=_date(i.not_after),
            ku=", ".join(i.key_usage) or "—")
        if i.self_signed:
            txt += "\n" + _("Certificato autofirmato: gli altri non lo riconosceranno automaticamente.")
        self.cert_info.setText(txt)

    def _accept(self) -> None:
        self.error.setText("")
        try:
            did = self._load()
        except self.digital.WrongCertificatePassword as e:
            self._failures += 1
            self.password.clear()
            self.error.setText(str(e))
            if self._failures >= 5:   # ritardo crescente dopo 5 tentativi
                delay = min(30, 2 ** (self._failures - 5))
                self.ok.setEnabled(False)
                QTimer.singleShot(delay * 1000, lambda: self.ok.setEnabled(True))
            return
        except self.digital.SignError as e:
            self.error.setText(str(e))
            return
        self._show_info(did)
        if not self._issuer_allowed(did.info):
            self.error.setText(_("Il campo non accetta certificati emessi da «{who}».").format(who=did.info.issuer))
            return
        try:
            warnings = self.digital.check_usable(did, allow_expired=self.allow_expired.isChecked())
        except self.digital.CertificateNotUsable as e:
            self.error.setText(str(e))
            if not did.info.is_valid_now():
                self.allow_expired.show()
            return
        if self.allow_expired.isChecked() and not did.info.is_valid_now():
            if QMessageBox.question(self, _("Certificato scaduto"), _(
                    "La firma risulterà non valida. Procedere solo per prova?")) != QMessageBox.StandardButton.Yes:
                return
        appearance = None
        if self.visible:
            appearance = self.current_profile().appearance()
            eid = self.graphic_combo.currentData()
            if eid and appearance["layout"] in ("grafica", "grafica_testo"):
                png = self._graphic_png(eid)
                if png:
                    appearance["image_png"] = png
        tsa = self.tsa.currentData()
        self.options = self.digital.SignOptions(
            reason=self.reason.currentText().strip(), location=self.location.text().strip(),
            contact=self.contact.text().strip(), appearance=appearance,
            timestamp_url=tsa[0] if tsa else None, timestamp_timeout=float(tsa[1]) if tsa else 15.0,
            timestamp_auth=self.tsa_auth(tsa),
            certify=self.certify, docmdp_level=int(self.level.currentData()),
            allow_expired=self.allow_expired.isChecked(),
            lock_fields=[f.strip() for f in self.lock.text().split(",") if f.strip()] or None)
        self.digital_id = did
        self.warnings = warnings
        s = self.c.ctx.settings()
        s.setValue("sign/location", self.location.text().strip())
        s.setValue("sign/contact", self.contact.text().strip())
        self.accept()

    def tsa_auth(self, tsa) -> tuple[str, str] | None:
        """Credenziali della TSA: password dal portachiavi, altrimenti chiesta (mai salvata su file)."""
        if not tsa or len(tsa) < 3 or not tsa[2]:
            return None
        pw = self.store.tsa_password(tsa[0])
        if pw is None:
            from PySide6.QtWidgets import QInputDialog
            pw, ok = QInputDialog.getText(self, _("Server marca temporale"), _(
                "Password per «{u}» su {url}:").format(u=tsa[2], url=tsa[0]), QLineEdit.EchoMode.Password)
            if not ok:
                return None
        return (tsa[2], pw)

    def current_profile(self, name: str = ""):
        return self.store.AppearanceProfile(
            name=name, layout=self.layout_combo.currentData(),
            show=[k for k, cb in self.show_checks.items() if cb.isChecked()],
            graphic=self.graphic_combo.currentData(), date_format=self.date_combo.currentData(),
            label_language=self.lang_combo.currentData())

    def update_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        from datetime import datetime
        info = self.digital_id.info if self.digital_id is not None else getattr(self, "_preview_info", None)
        name = info.subject if info else (self.c.default_name() or _("Nome del titolare"))
        prof = self.current_profile()
        opts = self.digital.SignOptions(reason=self.reason.currentText(), location=self.location.text())

        class _Info:
            subject = name
            subject_dn = info.subject_dn if info else f"CN={name}"
        lines = self.digital._appearance_lines(_Info, opts, datetime.now().astimezone(), prof.show,
                                               prof.appearance())
        pix = None
        eid = self.graphic_combo.currentData()
        if eid and prof.layout in ("grafica", "grafica_testo"):
            lib = self.c.library(interactive=False)
            try:
                from PySide6.QtCore import QSize

                from .ui_create import item_pixmap
                pix = item_pixmap(lib.load_item(eid), QSize(240, 80)) if lib else None
            except Exception:
                pix = None
        self.preview.set_content(prof.layout, lines, pix)

    def _apply_profile(self) -> None:
        p = self.profile_combo.currentData()
        if p is None:
            return
        i = self.layout_combo.findData(p.layout)
        if i >= 0:
            self.layout_combo.setCurrentIndex(i)
        j = self.graphic_combo.findData(p.graphic)
        self.graphic_combo.setCurrentIndex(max(0, j))
        if hasattr(self, "date_combo"):
            self.date_combo.setCurrentIndex(max(0, self.date_combo.findData(p.date_format)))
            self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(p.label_language)))
        for k, cb in self.show_checks.items():
            cb.setChecked(k in p.show)

    def _save_profile(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, _("Salva aspetto"), _("Nome dell'aspetto:"))
        if not ok or not name.strip():
            return
        prof = self.current_profile(name.strip())
        try:
            self.store.AppearanceProfiles().save(prof)
        except self.store.StoreError as e:
            self.error.setText(str(e))
            return
        self.profile_combo.addItem(prof.name, prof)
        self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)

    def _graphic_png(self, entry_id: str) -> bytes | None:
        """PNG della firma grafica (tracciati resi in immagine) per l'aspetto."""
        lib = self.c.library(interactive=False)
        if lib is None:
            return None
        try:
            item = lib.load_item(entry_id)
        except Exception:
            return None
        if item.png:
            return item.png
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize

        from .ui_create import item_pixmap
        h = 240
        pm = item_pixmap(item, QSize(int(h * item.aspect), h))
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.toImage().save(buf, "PNG")
        return bytes(ba.data())


class AppearancePreview(QWidget):
    """Anteprima approssimativa dell'aspetto (proporzioni 200×60 pt, sfondo bianco)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 90)
        self.layout_key = "testo"
        self.lines: list[str] = []
        self.pixmap = None
        self.setAccessibleName(_("Anteprima dell'aspetto della firma"))

    def set_content(self, layout_key: str, lines: list[str], pixmap) -> None:
        self.layout_key, self.lines, self.pixmap = layout_key, lines, pixmap
        self.setToolTip("\n".join(lines))
        self.update()

    def paintEvent(self, _ev) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFont, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, QColor(255, 255, 255))
        p.setPen(QColor(160, 168, 180))
        p.drawRect(r)
        text_r = r.adjusted(6, 4, -6, -4)
        if self.layout_key in ("grafica", "grafica_testo") and self.pixmap is not None:
            g_r = QRectF(text_r.left(), text_r.top(), text_r.width() / (1 if self.layout_key == "grafica" else 2),
                         text_r.height())
            pm = self.pixmap.scaled(int(g_r.width()), int(g_r.height()), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(int(g_r.left()), int(g_r.top() + (g_r.height() - pm.height()) / 2), pm)
            text_r.setLeft(g_r.right() + 6)
        if self.layout_key != "grafica" and self.lines:
            p.setPen(QColor(20, 20, 20))
            lines = list(self.lines)
            if self.layout_key == "nome_grande":
                f = QFont("Helvetica")
                f.setPointSizeF(15)
                p.setFont(f)
                half = QRectF(text_r.left(), text_r.top(), text_r.width() / 2, text_r.height())
                name = lines.pop(0).split(" da ")[-1].split(" by ")[-1] if lines else ""
                p.drawText(half, Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, name)
                text_r.setLeft(half.right() + 4)
            size = 9.0
            f = QFont("Helvetica")
            while size >= 5:
                f.setPointSizeF(size)
                p.setFont(f)
                need = p.fontMetrics().lineSpacing() * len(lines)
                if need <= text_r.height():
                    break
                size -= 0.5
            p.drawText(text_r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "\n".join(lines))
        p.end()


# ============================================================== campo firma
class FieldDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Nuovo campo firma"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(_("Firma"))
        form.addRow(_("Nome:"), self.name)
        self.tooltip = QLineEdit()
        self.tooltip.setPlaceholderText(_("es. Firma del cliente"))
        form.addRow(_("Descrizione:"), self.tooltip)
        self.required = QCheckBox(_("Obbligatorio"))
        form.addRow("", self.required)
        self.tsa = QLineEdit()
        self.tsa.setPlaceholderText("https://…")
        form.addRow(_("Server marca temporale:"), self.tsa)
        self.tsa_required = QCheckBox(_("Marca temporale obbligatoria"))
        form.addRow("", self.tsa_required)
        self.reasons = QLineEdit()
        self.reasons.setPlaceholderText(_("motivi separati da punto e virgola (facoltativo)"))
        form.addRow(_("Motivi ammessi:"), self.reasons)
        irow = QHBoxLayout()
        self.issuer_label = QLabel(_("Qualsiasi"))
        irow.addWidget(self.issuer_label, 1)
        b_issuer = QPushButton(_("Scegli certificato…"))
        b_issuer.clicked.connect(self._choose_issuer)
        irow.addWidget(b_issuer)
        form.addRow(_("Emittente ammesso:"), irow)
        self.issuers: list = []
        self.mdp = QComboBox()
        self.mdp.addItem(_("Nessuno (firma di approvazione)"), None)
        for lvl, label in LEVELS:
            self.mdp.addItem(_(label), lvl)
        form.addRow(_("Certificazione richiesta:"), self.mdp)
        self.digest = QComboBox()
        self.digest.addItem(_("Qualsiasi"), None)
        for alg in ("sha256", "sha384", "sha512"):
            self.digest.addItem(alg.upper().replace("SHA", "SHA-"), [alg])
        form.addRow(_("Algoritmo di hash richiesto:"), self.digest)
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(_("solo questa pagina; es. «tutte» o «1-3,7»"))
        form.addRow(_("Duplica sulle pagine:"), self.pages)
        self.lock_action = QComboBox()
        for key, label in [(None, _("Nessun campo")), ("All", _("Tutti i campi")),
                           ("Include", _("Solo i campi indicati")), ("Exclude", _("Tutti tranne quelli indicati"))]:
            self.lock_action.addItem(label, key)
        form.addRow(_("Dopo la firma blocca:"), self.lock_action)
        self.lock_fields = QLineEdit()
        self.lock_fields.setPlaceholderText(_("nomi dei campi separati da virgola"))
        form.addRow(_("Campi:"), self.lock_fields)
        lay.addLayout(form)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        lay.addWidget(self.warning)
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _choose_issuer(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Certificato dell'emittente ammesso"), "", _(CERT_FILTER))
        if not path:
            return
        from . import store
        try:
            ders = store._parse_certificates(Path(path).read_bytes())
            from .digital import cert_info_from_der
            self.issuers = [cert_info_from_der(d) for d in ders]
        except Exception as e:
            self.error.setText(str(e) or _("Certificato non leggibile."))
            return
        self.issuer_label.setText(", ".join(c.subject for c in self.issuers) or _("Qualsiasi"))

    def allowed_reasons(self) -> list[str] | None:
        return [r.strip() for r in self.reasons.text().split(";") if r.strip()] or None

    def lock_spec(self) -> tuple[str, list[str] | None]:
        action = self.lock_action.currentData()
        names = [f.strip() for f in self.lock_fields.text().split(",") if f.strip()]
        if action is None:
            return "Include", None
        return action, (names if action != "All" else None)

    def page_list(self, page_count: int, current: int) -> list[int]:
        """Pagine su cui creare il campo (sempre compresa quella corrente)."""
        text = self.pages.text().strip()
        if not text:
            return [current]
        pages = g.parse_page_range(text, page_count, current)
        return sorted(set(pages) | {current})

    def _accept(self) -> None:
        if self.pages.text().strip():
            try:
                g.parse_page_range(self.pages.text(), 10 ** 6)
            except ValueError as e:
                self.error.setText(str(e))
                return
        if self.lock_action.currentData() in ("Include", "Exclude") and not self.lock_fields.text().strip():
            self.error.setText(_("Indica i nomi dei campi da bloccare."))
            return
        if self.tsa_required.isChecked() and not self.tsa.text().strip().startswith(("http://", "https://")):
            self.error.setText(_("Per rendere obbligatoria la marca temporale indica l'indirizzo del server "
                                 "(http:// o https://)."))
            return
        self.accept()


# ========================================================== ID autofirmato
class SelfSignedDialog(QDialog):
    def __init__(self, parent, controller):
        super().__init__(parent)
        from . import digital, store
        self.c = controller
        self.digital = digital
        self.store = store
        self.created_path: str | None = None
        self.info = None
        self.setWindowTitle(_("Crea ID digitale autofirmato"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        note = QLabel(_("Un ID autofirmato va bene per uso interno o per prova: gli altri non lo "
                        "riconosceranno automaticamente finché non considerano attendibile il tuo certificato."))
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.cn = QLineEdit(controller.default_name())
        form.addRow(_("Nome e cognome:"), self.cn)
        self.org = QLineEdit()
        form.addRow(_("Organizzazione:"), self.org)
        self.unit = QLineEdit()
        form.addRow(_("Unità:"), self.unit)
        self.email = QLineEdit()
        form.addRow(_("E-mail:"), self.email)
        self.country = QLineEdit("IT")
        self.country.setMaxLength(2)
        form.addRow(_("Paese (2 lettere):"), self.country)
        self.alg = QComboBox()
        self.alg.addItem("ECDSA P-256", "ec-p256")
        self.alg.addItem("RSA 3072", "rsa-3072")
        self.alg.addItem("RSA 4096", "rsa-4096")
        form.addRow(_("Algoritmo:"), self.alg)
        self.years = QSpinBox()
        self.years.setRange(1, 10)
        self.years.setValue(3)
        self.years.setSuffix(_(" anni"))
        form.addRow(_("Validità:"), self.years)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Password:"), self.pw1)
        self.strength = QLabel("")
        form.addRow("", self.strength)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Conferma password:"), self.pw2)
        prow = QHBoxLayout()
        default_dir = store.data_dir() / "sign" / "ids"
        self.path = QLineEdit(str(default_dir / "mio-id.p12"))
        prow.addWidget(self.path, 1)
        b = QPushButton(_("Sfoglia…"))
        b.clicked.connect(self._browse)
        prow.addWidget(b)
        form.addRow(_("File:"), prow)
        lay.addLayout(form)
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox()
        box.addButton(_("Crea"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.pw1.textChanged.connect(self._strength)

    def _strength(self, pw: str) -> None:
        score, desc = self.digital.password_strength(pw)
        colors = ["danger", "danger", "warning", "success", "success"]
        self.strength.setText(desc)
        self.strength.setStyleSheet(f"color: {theme.hex_color(colors[max(0, min(4, score))])}")

    def _browse(self) -> None:
        path, _f = QFileDialog.getSaveFileName(self, _("Salva l'ID digitale"), self.path.text(), _(P12_FILTER))
        if path:
            if not path.lower().endswith((".p12", ".pfx")):
                path += ".p12"
            self.path.setText(path)

    def _accept(self) -> None:
        self.error.setText("")
        if not self.cn.text().strip():
            self.error.setText(_("Il nome è obbligatorio."))
            return
        if self.pw1.text() != self.pw2.text():
            self.error.setText(_("La password e la conferma non coincidono."))
            return
        score, desc = self.digital.password_strength(self.pw1.text())
        if score < 2 and QMessageBox.question(self, _("Password debole"), _(
                "La password è debole ({d}). Usarla comunque?").format(d=desc)) != QMessageBox.StandardButton.Yes:
            return
        path = Path(self.path.text().strip()).expanduser()
        if path.exists() and QMessageBox.question(self, _("File esistente"), _(
                "«{p}» esiste già. Sovrascriverlo?").format(p=path)) != QMessageBox.StandardButton.Yes:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            info = self.digital.create_self_signed(
                path, self.pw1.text(), common_name=self.cn.text().strip(), organization=self.org.text().strip(),
                unit=self.unit.text().strip(), email=self.email.text().strip(),
                country=self.country.text().strip().upper(), algorithm=self.alg.currentData(),
                years=self.years.value())
            self.store.IDRegistry().add(path, info)
        except (self.digital.SignError, OSError) as e:
            self.error.setText(str(e) if not isinstance(e, OSError) else
                               _("Impossibile scrivere in «{p}»: {e}").format(p=path.parent, e=e.strerror))
            return
        self.created_path = str(path)
        self.info = info
        if QMessageBox.question(self, _("ID digitale creato"), _(
                "ID creato e registrato.\n\nVuoi esportare il certificato pubblico da consegnare ai destinatari, "
                "così potranno considerarlo attendibile?")) == QMessageBox.StandardButton.Yes:
            export_certificate_dialog(self, self.digital, info)
        self.accept()


def export_certificate_dialog(parent, digital, info) -> None:
    path, flt = QFileDialog.getSaveFileName(parent, _("Esporta certificato"), f"{info.subject}.pem",
                                            "PEM (*.pem);;DER (*.cer)")
    if not path:
        return
    fmt = "der" if path.lower().endswith((".cer", ".der")) or "DER" in flt else "pem"
    try:
        digital.export_certificate(info, path, fmt)
    except Exception as e:
        QMessageBox.warning(parent, _("Esporta certificato"), str(e))


# ================================================== ID e certificati attendibili
class IDsDialog(QDialog):
    def __init__(self, parent, controller):
        super().__init__(parent)
        from . import digital, store
        self.c = controller
        self.digital = digital
        self.store = store
        self.setWindowTitle(_("ID digitali e certificati attendibili"))
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        # --- ID
        w1 = QWidget()
        l1 = QHBoxLayout(w1)
        self.ids = QListWidget()
        l1.addWidget(self.ids, 1)
        c1 = QVBoxLayout()
        for text, fn in [(_("Aggiungi file…"), self._add_id), (_("Crea autofirmato…"), self._create),
                         (_("Imposta predefinito"), self._default_id), (_("Ricollega file…"), self._relink),
                         (_("Esporta certificato…"), self._export_id), (_("Rimuovi dall'elenco"), self._remove_id)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            c1.addWidget(b)
        c1.addStretch(1)
        l1.addLayout(c1)
        tabs.addTab(w1, _("I miei ID digitali"))
        # --- attendibili
        w2 = QWidget()
        l2 = QHBoxLayout(w2)
        self.trusted = QListWidget()
        l2.addWidget(self.trusted, 1)
        c2 = QVBoxLayout()
        for text, fn in [(_("Importa…"), self._import_cert), (_("Dettagli"), self._cert_details),
                         (_("Rimuovi"), self._remove_cert)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            c2.addWidget(b)
        c2.addStretch(1)
        l2.addLayout(c2)
        tabs.addTab(w2, _("Certificati attendibili"))
        # --- TSA
        w3 = QWidget()
        l3 = QHBoxLayout(w3)
        self.tsas = QListWidget()
        l3.addWidget(self.tsas, 1)
        c3 = QVBoxLayout()
        for text, fn in [(_("Aggiungi…"), self._add_tsa), (_("Imposta predefinito"), self._default_tsa),
                         (_("Rimuovi"), self._remove_tsa)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            c3.addWidget(b)
        c3.addStretch(1)
        l3.addLayout(c3)
        tabs.addTab(w3, _("Server marca temporale"))
        # --- impostazioni di verifica
        w4 = QWidget()
        f4 = QFormLayout(w4)
        settings = controller.ctx.settings()
        self.auto_verify = QCheckBox(_("Verifica le firme all'apertura dei documenti"))
        self.auto_verify.setChecked(controller.auto_verify())
        f4.addRow(self.auto_verify)
        self.revocation = QComboBox()
        self.revocation.addItem(_("Mai (solo dati incorporati, nessuna connessione)"), "mai")
        self.revocation.addItem(_("Sempre (interroga OCSP e CRL indicati nei certificati)"), "sempre")
        self.revocation.setCurrentIndex(max(0, self.revocation.findData(controller.revocation_mode())))
        f4.addRow(_("Controllo della revoca online:"), self.revocation)
        self.system_certs = QCheckBox(_("Considera attendibili anche i certificati del sistema"))
        self.system_certs.setToolTip(_("Sono pensati per i siti web, non per le firme: attivali solo se servono."))
        self.system_certs.setChecked(str(settings.value("sign/system_certs", "false")).lower() == "true")
        self.system_certs.toggled.connect(
            lambda v: settings.setValue("sign/system_certs", "true" if v else "false"))
        f4.addRow(self.system_certs)
        self.auto_verify.toggled.connect(lambda v: settings.setValue("sign/auto_verify", "true" if v else "false"))
        self.revocation.currentIndexChanged.connect(
            lambda _i: settings.setValue("sign/revocation", self.revocation.currentData()))
        tabs.addTab(w4, _("Verifica"))
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.refresh()

    def refresh(self) -> None:
        self.ids.clear()
        for e in self.store.IDRegistry().list():
            exp = e.expires
            text = f"{e.alias}  ·  {_('scade il')} {_date(exp)}" + ("  ★" if e.default else "")
            if e.missing:
                text += "  " + _("(file non trovato)")
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, e)
            self.ids.addItem(it)
        self.trusted.clear()
        for t in self.store.TrustStore().list():
            it = QListWidgetItem(f"{t.subject}  ·  {_('scade il')} {_date(t.not_after)}")
            it.setData(Qt.ItemDataRole.UserRole, t)
            self.trusted.addItem(it)
        self.tsas.clear()
        for s in self.store.TSAConfig().list():
            it = QListWidgetItem(f"{s.name} — {s.url}" + ("  ★" if s.default else ""))
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.tsas.addItem(it)

    def _cur(self, lst):
        it = lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _run(self, fn) -> None:
        self.error.setText("")
        try:
            fn()
        except Exception as e:
            self.error.setText(str(e))
        self.refresh()

    def _add_id(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Aggiungi ID digitale"), "", _(P12_FILTER))
        if not path:
            return
        from PySide6.QtWidgets import QInputDialog
        pw, ok = QInputDialog.getText(self, _("Password"), _("Password dell'ID digitale:"),
                                      QLineEdit.EchoMode.Password)
        if not ok:
            return
        self._run(lambda: self.store.IDRegistry().add(path, self.digital.load_pkcs12(path, pw).info))

    def _create(self) -> None:
        SelfSignedDialog(self, self.c).exec()
        self.refresh()

    def _default_id(self) -> None:
        e = self._cur(self.ids)
        if e:
            self._run(lambda: self.store.IDRegistry().set_default(e.fingerprint))

    def _relink(self) -> None:
        e = self._cur(self.ids)
        if not e:
            return
        path, _f = QFileDialog.getOpenFileName(self, _("Ricollega ID digitale"), "", _(P12_FILTER))
        if path:
            self._run(lambda: self.store.IDRegistry().relink(e.fingerprint, path))

    def _export_id(self) -> None:
        e = self._cur(self.ids)
        if not e:
            return
        from PySide6.QtWidgets import QInputDialog
        pw, ok = QInputDialog.getText(self, _("Password"), _("Password dell'ID digitale:"),
                                      QLineEdit.EchoMode.Password)
        if ok:
            try:
                export_certificate_dialog(self, self.digital, self.digital.load_pkcs12(e.path, pw).info)
            except Exception as ex:
                self.error.setText(str(ex))

    def _remove_id(self) -> None:
        e = self._cur(self.ids)
        if e:
            self._run(lambda: self.store.IDRegistry().remove(e.fingerprint))

    def _import_cert(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Importa certificati"), "", _(CERT_FILTER))
        if path:
            self._run(lambda: self.store.TrustStore().import_file(path))

    def _cert_details(self) -> None:
        t = self._cur(self.trusted)
        if t:
            QMessageBox.information(self, _("Certificato"), certificate_details(t))

    def _remove_cert(self) -> None:
        t = self._cur(self.trusted)
        if t and QMessageBox.question(self, _("Rimuovi"), _("Rimuovere «{s}» dagli attendibili?").format(
                s=t.subject)) == QMessageBox.StandardButton.Yes:
            self._run(lambda: self.store.TrustStore().remove(t.sha256))

    def _add_tsa(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, _("Server marca temporale"), _("Indirizzo del server (URL):"))
        if not ok or not url.strip():
            return
        name, ok = QInputDialog.getText(self, _("Server marca temporale"), _("Nome:"), text=url.strip())
        if not ok:
            return
        user, ok = QInputDialog.getText(self, _("Server marca temporale"),
                                        _("Nome utente (vuoto se il server non lo richiede):"))
        if not ok:
            return
        if user.strip():
            pw, ok = QInputDialog.getText(self, _("Server marca temporale"), _("Password:"),
                                          QLineEdit.EchoMode.Password)
            if ok and pw and not self.store.store_tsa_password(url.strip(), pw):
                QMessageBox.information(self, _("Server marca temporale"), _(
                    "Il portachiavi del sistema non è disponibile: la password non viene salvata e sarà "
                    "chiesta a ogni firma."))
        self._run(lambda: self.store.TSAConfig().add(name, url, user=user))

    def _default_tsa(self) -> None:
        s = self._cur(self.tsas)
        if s:
            self._run(lambda: self.store.TSAConfig().set_default(s.url))

    def _remove_tsa(self) -> None:
        s = self._cur(self.tsas)
        if s:
            self._run(lambda: self.store.TSAConfig().remove(s.url))


def certificate_details(c) -> str:
    return _("Soggetto: {s}\nEmittente: {i}\nNumero di serie: {n}\nValido dal {a} al {b}\nUsi della chiave: {k}\n"
             "SHA-256: {h}\nSHA-1: {h1}").format(
        s=c.subject_dn, i=c.issuer_dn, n=c.serial, a=_date(c.not_before), b=_date(c.not_after),
        k=", ".join(c.key_usage) or "—", h=c.sha256, h1=c.sha1)


# ======================================================= ripetizione firma
class RepeatDialog(QDialog):
    """`preview(pages, anchor)` (facoltativo) restituisce [(pagina, QImage, ridotta)] per le miniature."""

    MAX_THUMBS = 8

    def __init__(self, parent, page_count: int, current: int, preview=None):
        super().__init__(parent)
        self.preview_fn = preview
        self.page_count = page_count
        self.current = current
        self.pages: list[int] = []
        self.anchor = "auto"
        self.setWindowTitle(_("Ripeti su più pagine"))
        self.setMinimumWidth(560 if preview is not None else 320)
        lay = QVBoxLayout(self)
        grp = QGroupBox(_("Pagine"))
        gl = QVBoxLayout(grp)
        self.r_all = QRadioButton(_("Tutte"))
        self.r_other = QRadioButton(_("Tutte tranne la corrente"))
        self.r_even = QRadioButton(_("Pari"))
        self.r_odd = QRadioButton(_("Dispari"))
        self.r_range = QRadioButton(_("Intervallo:"))
        self.r_other.setChecked(True)
        self.range = QLineEdit()
        self.range.setPlaceholderText(_("es. 1-3,7,10-"))
        for w in (self.r_all, self.r_other, self.r_even, self.r_odd):
            gl.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(self.r_range)
        row.addWidget(self.range, 1)
        gl.addLayout(row)
        lay.addWidget(grp)
        form = QFormLayout()
        self.anchor_combo = QComboBox()
        for key, label in [("auto", _("Angolo più vicino")), ("tl", _("In alto a sinistra")),
                           ("tr", _("In alto a destra")), ("bl", _("In basso a sinistra")),
                           ("br", _("In basso a destra")), ("abs", _("Stessa posizione assoluta"))]:
            self.anchor_combo.addItem(label, key)
        form.addRow(_("Ancoraggio:"), self.anchor_combo)
        lay.addLayout(form)
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setIconSize(QSize(96, 128))
        self.thumbs.setFixedHeight(170)
        self.thumbs.setFlow(QListWidget.Flow.LeftToRight)
        self.thumbs.setWrapping(False)
        self.thumbs.setAccessibleName(_("Anteprima delle pagine"))
        if preview is not None:
            lay.addWidget(self.thumbs)
        else:
            self.thumbs.hide()
        self.anchor_combo.currentIndexChanged.connect(lambda _i: self._update_preview())
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox()
        self.apply = box.addButton(_("Applica"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.range.textChanged.connect(lambda _t: (self.r_range.setChecked(True), self._validate()))
        for r in (self.r_all, self.r_other, self.r_even, self.r_odd, self.r_range):
            r.toggled.connect(lambda _c: self._validate())
        self._validate()

    def _spec(self) -> str:
        if self.r_all.isChecked():
            return "tutte"
        if self.r_other.isChecked():
            return "tutte tranne la corrente"
        if self.r_even.isChecked():
            return "pari"
        if self.r_odd.isChecked():
            return "dispari"
        return self.range.text()

    def _validate(self) -> bool:
        try:
            pages = g.parse_page_range(self._spec(), self.page_count, self.current)
        except ValueError as e:
            self.error.setText(str(e))
            self.apply.setEnabled(False)
            return False
        pages = [p for p in pages if p != self.current]
        if not pages:
            self.error.setText(_("Nessuna pagina oltre a quella corrente."))
            self.apply.setEnabled(False)
            return False
        self.error.setText("")
        self.apply.setEnabled(True)
        self.pages = pages
        self._update_preview()
        return True

    def _update_preview(self) -> None:
        if self.preview_fn is None or not self.pages:
            return
        from PySide6.QtGui import QIcon, QPixmap
        self.thumbs.clear()
        try:
            items = self.preview_fn(self.pages[:self.MAX_THUMBS], self.anchor_combo.currentData())
        except Exception:
            return
        for page, img, shrunk in items:
            label = _("Pag. {n}").format(n=page + 1) + (" " + _("(ridotta)") if shrunk else "")
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), label)
            self.thumbs.addItem(it)
        if len(self.pages) > self.MAX_THUMBS:
            self.thumbs.addItem(QListWidgetItem(_("… e altre {n}").format(n=len(self.pages) - self.MAX_THUMBS)))

    def _accept(self) -> None:
        if self._validate():
            self.anchor = self.anchor_combo.currentData()
            self.accept()


TRANSLATIONS_EN = {
    "ID digitale (*.p12 *.pfx)": "Digital ID (*.p12 *.pfx)",
    "Certificati (*.cer *.crt *.pem *.der *.p7b *.p7c)": "Certificates (*.cer *.crt *.pem *.der *.p7b *.p7c)",
    "Approvo il documento": "I approve this document", "Sono l'autore del documento": "I am the author of this document",
    "Ho revisionato il documento": "I have reviewed this document", "Accetto i termini indicati": "I accept the terms",
    "Nessuna modifica consentita": "No changes allowed",
    "Consentite solo compilazione dei moduli e firme": "Only form filling and signatures allowed",
    "Consentite compilazione dei moduli, firme e annotazioni": "Form filling, signatures and annotations allowed",
    "Certifica il documento": "Certify the document", "Firma digitale": "Digital signature",
    "ID digitale": "Digital ID", "Sfoglia…": "Browse…", "ID digitale:": "Digital ID:",
    "Password dell'ID digitale": "Digital ID password", "Password:": "Password:",
    "Inserisci la password per vedere i dati del certificato.": "Enter the password to see the certificate details.",
    "Motivo:": "Reason:", "Luogo:": "Location:", "Contatto:": "Contact:",
    "Firma grafica e testo": "Graphic signature and text", "Solo testo": "Text only",
    "Nome grande e dettagli": "Large name and details", "Solo firma grafica": "Graphic signature only",
    "Nessuna": "None", "Aspetto:": "Appearance:", "Personalizzato": "Custom", "Nome": "Name", "Data": "Date",
    "Motivo": "Reason", "Luogo": "Location", "Nome distinto": "Distinguished name", "Etichette": "Labels",
    "Salva come aspetto…": "Save as appearance…", " (da completare)": " (to be completed)",
    "Profilo aspetto:": "Appearance profile:", "Disposizione:": "Layout:", "Mostra:": "Show:",
    "Salva aspetto": "Save appearance", "Etichette in:": "Labels in:", "Formato data:": "Date format:",
    "Anteprima:": "Preview:", "Nome del titolare": "Holder name",
    "Anteprima dell'aspetto della firma": "Signature appearance preview", "Nome dell'aspetto:": "Appearance name:", "Firma grafica:": "Graphic signature:",
    "Nessuna marca temporale": "No timestamp",
    "Il campo richiede una marca temporale da questo server.": "The field requires a timestamp from this server.",
    "Marca temporale:": "Timestamp:", "Modifiche consentite:": "Allowed changes:",
    "Firma comunque con un certificato scaduto (solo per prove: la firma risulterà non valida)":
        "Sign anyway with an expired certificate (testing only: the signature will be invalid)",
    "Certifica e salva…": "Certify and save…", "Firma e salva…": "Sign and save…", "Annulla": "Cancel",
    " (file non trovato)": " (file not found)", "Crea…": "Create…",
    "Crea un ID digitale autofirmato": "Create a self-signed digital ID",
    "Nessun ID digitale: usa «Sfoglia…» o «Crea…»": "No digital ID: use “Browse…” or “Create…”", "Scegli l'ID digitale": "Choose the digital ID",
    "Scegli un ID digitale (file .p12 o .pfx).": "Choose a digital ID (.p12 or .pfx file).",
    "Titolare: {s}\nEmittente: {iss}\nValido dal {a} al {b}\nUsi della chiave: {ku}":
        "Holder: {s}\nIssuer: {iss}\nValid from {a} to {b}\nKey usage: {ku}",
    "Certificato autofirmato: gli altri non lo riconosceranno automaticamente.":
        "Self-signed certificate: others will not recognise it automatically.",
    "Certificato scaduto": "Expired certificate",
    "La firma risulterà non valida. Procedere solo per prova?": "The signature will be invalid. Proceed for testing only?",
    "Nuovo campo firma": "New signature field", "Firma": "Signature", "Nome:": "Name:",
    "es. Firma del cliente": "e.g. Customer signature", "Descrizione:": "Description:",
    "Obbligatorio": "Required", "Server marca temporale:": "Timestamp server:",
    "Marca temporale obbligatoria": "Timestamp required",
    "Per rendere obbligatoria la marca temporale indica l'indirizzo del server (http:// o https://).":
        "To require a timestamp, enter the server address (http:// or https://).",
    "Crea ID digitale autofirmato": "Create self-signed digital ID",
    "Un ID autofirmato va bene per uso interno o per prova: gli altri non lo riconosceranno automaticamente "
    "finché non considerano attendibile il tuo certificato.":
        "A self-signed ID is fine for internal use or testing: others will not recognise it automatically until "
        "they trust your certificate.",
    "Nome e cognome:": "Full name:", "Organizzazione:": "Organisation:", "Unità:": "Unit:", "E-mail:": "E-mail:",
    "Paese (2 lettere):": "Country (2 letters):", "Algoritmo:": "Algorithm:", " anni": " years",
    "Validità:": "Validity:", "Conferma password:": "Confirm password:", "File:": "File:",
    "Crea": "Create", "Salva l'ID digitale": "Save the digital ID", "Il nome è obbligatorio.": "The name is required.",
    "La password e la conferma non coincidono.": "The password and its confirmation do not match.",
    "Password debole": "Weak password", "La password è debole ({d}). Usarla comunque?":
        "The password is weak ({d}). Use it anyway?",
    "File esistente": "Existing file", "«{p}» esiste già. Sovrascriverlo?": "“{p}” already exists. Overwrite it?",
    "Impossibile scrivere in «{p}»: {e}": "Cannot write to “{p}”: {e}",
    "ID digitale creato": "Digital ID created",
    "ID creato e registrato.\n\nVuoi esportare il certificato pubblico da consegnare ai destinatari, così potranno "
    "considerarlo attendibile?": "ID created and registered.\n\nDo you want to export the public certificate to give "
                                 "to recipients, so they can trust it?",
    "Esporta certificato": "Export certificate",
    "ID digitali e certificati attendibili": "Digital IDs and trusted certificates",
    "Aggiungi file…": "Add file…", "Crea autofirmato…": "Create self-signed…", "Imposta predefinito": "Set as default",
    "Ricollega file…": "Relink file…", "Esporta certificato…": "Export certificate…",
    "Rimuovi dall'elenco": "Remove from list", "I miei ID digitali": "My digital IDs", "Importa…": "Import…",
    "Dettagli": "Details", "Rimuovi": "Remove", "Certificati attendibili": "Trusted certificates",
    "Aggiungi…": "Add…", "Server marca temporale": "Timestamp servers", "Chiudi": "Close", "scade il": "expires",
    "(file non trovato)": "(file not found)", "Aggiungi ID digitale": "Add digital ID", "Password": "Password",
    "Password dell'ID digitale:": "Digital ID password:", "Ricollega ID digitale": "Relink digital ID",
    "Importa certificati": "Import certificates", "Certificato": "Certificate",
    "Rimuovere «{s}» dagli attendibili?": "Remove “{s}” from the trusted certificates?",
    "Indirizzo del server (URL):": "Server address (URL):",
    "Soggetto: {s}\nEmittente: {i}\nNumero di serie: {n}\nValido dal {a} al {b}\nUsi della chiave: {k}\n"
    "SHA-256: {h}\nSHA-1: {h1}": "Subject: {s}\nIssuer: {i}\nSerial number: {n}\nValid from {a} to {b}\n"
                                 "Key usage: {k}\nSHA-256: {h}\nSHA-1: {h1}",
    "Ripeti su più pagine": "Repeat on several pages",
    "nomi dei campi separati da virgola": "field names separated by commas",
    " (non ammesso dal campo)": " (not allowed by the field)",
    "Questo campo accetta solo certificati emessi da: {who}": "This field only accepts certificates issued by: {who}",
    "Il campo non accetta certificati emessi da «{who}».": "The field does not accept certificates issued by “{who}”.",
    "motivi separati da punto e virgola (facoltativo)": "reasons separated by semicolons (optional)",
    "Motivi ammessi:": "Allowed reasons:", "Algoritmo di hash richiesto:": "Required hash algorithm:", "Nessuno (firma di approvazione)": "None (approval signature)",
    "Certificazione richiesta:": "Required certification:", "Duplica sulle pagine:": "Duplicate on pages:",
    "solo questa pagina; es. «tutte» o «1-3,7»": "this page only; e.g. “tutte” or “1-3,7”", "Qualsiasi": "Any", "Scegli certificato…": "Choose certificate…",
    "Emittente ammesso:": "Allowed issuer:", "Certificato dell'emittente ammesso": "Certificate of the allowed issuer",
    "Certificato non leggibile.": "Unreadable certificate.",
    "Questi campi del modulo non potranno più essere modificati senza invalidare la firma.":
        "These form fields can no longer be changed without invalidating the signature.",
    "Blocca campi dopo la firma:": "Lock fields after signing:", "Nessun campo": "No fields",
    "Tutti i campi": "All fields", "Solo i campi indicati": "Only the listed fields",
    "Tutti tranne quelli indicati": "All except the listed ones", "Dopo la firma blocca:": "After signing lock:",
    "Campi:": "Fields:", "Indica i nomi dei campi da bloccare.": "Enter the names of the fields to lock.",
    "Verifica le firme all'apertura dei documenti": "Verify signatures when opening documents",
    "Mai (solo dati incorporati, nessuna connessione)": "Never (embedded data only, no connection)",
    "Sempre (interroga OCSP e CRL indicati nei certificati)": "Always (query the OCSP and CRL in the certificates)",
    "Controllo della revoca online:": "Online revocation check:",
    "Considera attendibili anche i certificati del sistema": "Also trust the system certificates",
    "Sono pensati per i siti web, non per le firme: attivali solo se servono.":
        "They are meant for websites, not signatures: enable them only if needed.", "Verifica": "Verification", "Pagine": "Pages", "Tutte": "All",
    "Tutte tranne la corrente": "All except the current one", "Pari": "Even", "Dispari": "Odd",
    "Intervallo:": "Range:", "es. 1-3,7,10-": "e.g. 1-3,7,10-", "Angolo più vicino": "Nearest corner",
    "In alto a sinistra": "Top left", "In alto a destra": "Top right", "In basso a sinistra": "Bottom left",
    "In basso a destra": "Bottom right", "Stessa posizione assoluta": "Same absolute position",
    "Ancoraggio:": "Anchor:", "Applica": "Apply",
    "Nessuna pagina oltre a quella corrente.": "No pages other than the current one.",
    "Password per «{u}» su {url}:": "Password for “{u}” on {url}:",
    "Nome utente (vuoto se il server non lo richiede):": "User name (empty if the server does not require it):",
    "Il portachiavi del sistema non è disponibile: la password non viene salvata e sarà chiesta a ogni firma.":
        "The system keyring is not available: the password is not saved and will be asked at each signature.",
    "Anteprima delle pagine": "Page preview", "Pag. {n}": "P. {n}", "(ridotta)": "(shrunk)",
    "… e altre {n}": "… and {n} more",
}
