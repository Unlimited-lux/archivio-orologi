"""Pannello laterale «Firme»: stato di ogni firma e dei campi da firmare (SIG-18/20)."""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout,
    QWidget,
)

from foliant.api import _, icon, theme

STATUS_STYLE = {
    "valida": ("shield-check", "success"),
    "non valida": ("shield-x", "danger"),
    "indeterminata": ("shield-question", "warning"),
    "da firmare": ("pen-line", "text_muted"),
    "in corso": ("loader-circle", "text_muted"),
}


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    try:
        return dt.astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
    except Exception:
        return str(dt)


def status_details(s) -> str:
    """Dettagli testuali di una firma (anche per «Copia dettagli» e il rapporto)."""
    lines = [f"{s.label} — {s.field_name}"]
    lines += [f"  • {r}" for r in s.reasons]
    src = _("attestata da marca temporale") if s.time_source == "marca temporale" else \
        _("dichiarata dal firmatario")
    lines += [
        _("Firmatario: {v}").format(v=s.signer or "—"),
        _("Ora: {t} ({src})").format(t=_fmt_time(s.signing_time), src=src),
        _("Tipo: {v}").format(v=_(s.kind)),
    ]
    if s.docmdp_level:
        lines.append(_("Livello di certificazione: {v}").format(v=s.docmdp_level))
    if s.pades_level:
        lines.append(_("Livello PAdES: {v}").format(v=s.pades_level))
    if s.timestamp_tsa:
        lines.append(_("Autorità di marca temporale: {v}").format(v=s.timestamp_tsa))
    for key, val in ((_("Motivo"), s.reason), (_("Luogo"), s.location), (_("Contatto"), s.contact)):
        if val:
            lines.append(f"{key}: {val}")
    lines.append(_("Pagina: {v}").format(v="—" if s.invisible or s.page is None else s.page + 1))
    lines.append(_("Integrità: {v}").format(v=_("confermata") if s.intact else _("non confermata")))
    lines.append(s.modifications)
    lines += [f"  • {d}" for d in s.modification_details]
    if getattr(s, "md_algorithm", ""):
        lines.append(_("Algoritmo di hash: {v}").format(v=s.md_algorithm))
    if s.chain:
        lines.append(_("Catena dei certificati:"))
        for c in s.chain:
            lines.append(f"  – {c.subject} ({_('emesso da')} {c.issuer}, {_('scade il')} "
                         f"{c.not_after.strftime('%d/%m/%Y')})")
    return "\n".join(lines)


def report_xml(statuses) -> str:
    """Rapporto di verifica in XML strutturato (esiti nel vocabolario ETSI)."""
    main = {"valida": "TOTAL-PASSED", "non valida": "TOTAL-FAILED", "indeterminata": "INDETERMINATE"}
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<ValidationReport generator="Foliant" created="{datetime.now().astimezone().isoformat()}">']
    for s in statuses:
        out.append(f'  <SignatureValidationReport field="{escape(s.field_name)}">')
        out.append(f"    <SignatureIdentifier><Signer>{escape(s.signer or '')}</Signer>"
                   f"<SigningTime source=\"{escape(s.time_source)}\">"
                   f"{s.signing_time.isoformat() if s.signing_time else ''}</SigningTime></SignatureIdentifier>")
        out.append(f"    <SignatureValidationStatus><MainIndication>{main.get(s.status, 'INDETERMINATE')}"
                   f"</MainIndication>")
        for r in s.reasons:
            out.append(f"      <AssociatedValidationReportData>{escape(r)}</AssociatedValidationReportData>")
        out.append("    </SignatureValidationStatus>")
        out.append(f"    <SignatureQuality level=\"{escape(s.pades_level or '')}\" kind=\"{escape(s.kind)}\"/>")
        out.append(f"    <Modifications>{escape(s.modifications)}</Modifications>")
        out.append("  </SignatureValidationReport>")
    out.append("</ValidationReport>")
    return "\n".join(out)


class ButtonFlow(QWidget):
    """Pulsanti disposti in griglia: due per riga solo se entrambi stanno nella larghezza
    senza scendere sotto il loro minimumSizeHint (NFR-24), altrimenti uno per riga."""

    def __init__(self, buttons, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout
        self.buttons = list(buttons)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self._columns = None
        for b in self.buttons:
            b.setParent(self)        # senza genitore isHidden() sarebbe vero prima del primo show
            b.installEventFilter(self)
        self.relayout()

    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.relayout(force=True)
        return False

    def _fits_two(self, width: int) -> bool:
        visible = [b for b in self.buttons if not b.isHidden()]
        sp = self.grid.horizontalSpacing() if self.grid.horizontalSpacing() >= 0 else 6
        for a, b in zip(visible[0::2], visible[1::2]):
            need = max(a.minimumSizeHint().width(), a.sizeHint().width()) + \
                max(b.minimumSizeHint().width(), b.sizeHint().width()) + sp
            if need > width:
                return False
        return True

    def relayout(self, force: bool = False) -> None:
        # finché non è visibile la larghezza non è nota: una colonna (mai pulsanti compressi)
        cols = 2 if self.isVisible() and self._fits_two(max(self.width(), 1)) else 1
        if cols == self._columns and not force:
            return
        self._columns = cols
        for b in self.buttons:
            self.grid.removeWidget(b)
        visible = [b for b in self.buttons if not b.isHidden()]
        for i, b in enumerate(visible):
            self.grid.addWidget(b, i // cols, i % cols)
        for c in range(2):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        self.updateGeometry()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        widths = [max(b.minimumSizeHint().width(), b.sizeHint().width()) for b in self.buttons if not b.isHidden()]
        return QSize(max(widths or [0]), super().minimumSizeHint().height())

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.relayout()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.relayout()


class SignaturesPanel(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.view = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.banner = QLabel(_("Nessuna firma"))
        self.banner.setWordWrap(True)
        self.banner.setObjectName("signBanner")
        self.banner.setAccessibleName(_("Stato complessivo delle firme"))
        lay.addWidget(self.banner)
        self.btn_sign_now = QPushButton(_("Firma ora"))
        self.btn_next = QPushButton(_("Campo successivo"))
        self.btn_verify = QPushButton(_("Verifica di nuovo"))
        self.btn_online = QPushButton(_("Verifica online"))
        self.btn_online.setToolTip(_("Controlla la revoca dei certificati presso i servizi indicati nei certificati "
                                     "(richiede la rete)"))
        self.top_buttons = ButtonFlow([self.btn_sign_now, self.btn_next, self.btn_verify, self.btn_online])
        lay.addWidget(self.top_buttons)
        self.list = QListWidget()
        self.list.setAccessibleName(_("Firme e campi firma"))
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 2)
        self.details = QTextBrowser()
        self.details.setAccessibleName(_("Dettagli della firma"))
        lay.addWidget(self.details, 2)
        self.btn_goto = QPushButton(_("Vai al campo"))
        self.btn_signed = QPushButton(_("Versione firmata"))
        self.btn_signed.setToolTip(_("Apre il documento come era al momento della firma"))
        self.btn_trust = QPushButton(_("Considera attendibile"))
        self.btn_copy = QPushButton(_("Copia dettagli"))
        self.btn_report = QPushButton(_("Esporta rapporto…"))
        self.action_buttons = ButtonFlow([self.btn_goto, self.btn_signed, self.btn_trust, self.btn_copy,
                                          self.btn_report])
        lay.addWidget(self.action_buttons)
        self._next_index = 0
        self.btn_verify.clicked.connect(lambda: self.view and self.c.verify(self.view, force=True))
        self.btn_next.clicked.connect(self.next_field)
        self.btn_online.clicked.connect(lambda: self.view and self.c.verify(self.view, force=True, online=True))
        self.btn_sign_now.clicked.connect(self.sign_now)
        self.btn_goto.clicked.connect(self.goto)
        self.btn_signed.clicked.connect(lambda: self._with_status(lambda s: self.c.open_signed_version(self.view, s)))
        self.btn_trust.clicked.connect(lambda: self._with_status(lambda s: self.c.trust_signer(self.view, s)))
        self.btn_copy.clicked.connect(lambda: self._with_status(lambda s: self.c.copy_text(status_details(s))))
        self.btn_report.clicked.connect(self.export_report)
        self.list.currentRowChanged.connect(lambda _r: self._show_current())
        controller.stateChanged.connect(self._on_state)
        controller.statusSelected.connect(self.select_status)
        self.refresh()

    # ------------------------------------------------------------------ stato
    def set_view(self, view) -> None:
        self.view = view
        self._next_index = 0
        self.refresh()

    def _on_state(self, view) -> None:
        if view is self.view:
            self.refresh()

    def visible_empty_fields(self) -> list:
        if self.view is None:
            return []
        return [f for f in self.c.empty_fields(self.view) if not f.hidden]

    def refresh(self) -> None:
        self.list.clear()
        self.details.clear()
        view = self.view
        if view is None:
            self.banner.setText(_("Nessun documento aperto"))
            self._set_buttons()
            return
        st = self.c.state(view)
        if st.running:
            it = QListWidgetItem(icon("loader-circle"), _("Verifica in corso…"))
            self.list.addItem(it)
        for s in st.statuses:
            self._add_item(s.status, f"{s.label}: {s.signer or s.field_name}\n"
                                     f"{s.field_name} · {_fmt_time(s.signing_time)}", ("status", s))
        empties = self.visible_empty_fields()
        for f in empties:
            self._add_item("da firmare", _("Da firmare: {n}").format(n=f.tooltip or f.name) +
                           ("\n" + _("Obbligatorio") if f.required else "") +
                           (f"\n{_('Pagina')} {f.page + 1}" if f.page is not None else ""), ("field", f))
        if st.error:
            self.banner.setText(_("Verifica non riuscita: {e}").format(e=st.error))
        elif st.running:
            self.banner.setText(_("Verifica delle firme in corso…"))
        elif not st.statuses and not empties:
            self.banner.setText(_("Nessuna firma"))
        else:
            from . import digital
            text = digital.summary(st.statuses, empties)
            if view.document.is_modified and st.statuses:
                text += "\n" + _("Il documento ha modifiche non salvate: salvandolo, le firme non saranno più "
                                 "valide.")
            self.banner.setText(text)
        bad = any(s.status != "valida" for s in st.statuses) or bool(st.error)
        tone = "danger" if bad else "success" if st.statuses else "warning" if empties else "text_muted"
        c = theme.color(tone)
        self.banner.setStyleSheet(
            f"#signBanner {{ border: 1px solid {c.name()}; border-left: 4px solid {c.name()}; border-radius: 4px;"
            f" padding: 6px 8px; background: rgba({c.red()}, {c.green()}, {c.blue()}, 28);"
            f" color: {theme.hex_color('text')}; }}")
        if self.list.count():
            self.list.setCurrentRow(0)
        self._set_buttons()

    def _add_item(self, status: str, text: str, data) -> None:
        icon_name, color = STATUS_STYLE[status]
        it = QListWidgetItem(icon(icon_name, theme.hex_color(color)), text)
        it.setData(Qt.ItemDataRole.UserRole, data)
        it.setData(Qt.ItemDataRole.AccessibleTextRole, text.replace("\n", ", "))
        it.setForeground(theme.color(color) if status != "da firmare" else theme.color("text"))
        self.list.addItem(it)

    def select_status(self, view, status) -> None:
        if view is not self.view:
            return
        for i in range(self.list.count()):
            d = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if d and d[0] == "status" and d[1] is status:
                self.list.setCurrentRow(i)
                self.list.setFocus()
                return

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _show_current(self) -> None:
        cur = self.current()
        if cur is None:
            self.details.clear()
        elif cur[0] == "status":
            self.details.setPlainText(status_details(cur[1]))
        else:
            f = cur[1]
            txt = _("Campo firma vuoto «{n}».").format(n=f.name)
            if f.tooltip:
                txt += "\n" + f.tooltip
            if f.required:
                txt += "\n" + _("Firma obbligatoria.")
            if f.timestamp_required_url:
                txt += "\n" + _("Richiede una marca temporale da {u}.").format(u=f.timestamp_required_url)
            self.details.setPlainText(txt)
        self._set_buttons()

    def _set_buttons(self) -> None:
        cur = self.current()
        is_status = cur is not None and cur[0] == "status"
        st = self.c.state(self.view) if self.view is not None else None
        self.btn_verify.setEnabled(self.view is not None)
        self.btn_online.setVisible(bool(st and st.statuses))
        has_fields = bool(self.visible_empty_fields())
        self.btn_sign_now.setVisible(has_fields)
        self.btn_next.setVisible(has_fields)
        self.btn_goto.setEnabled(cur is not None and not (is_status and cur[1].invisible))
        self.btn_signed.setEnabled(is_status)
        self.btn_trust.setEnabled(is_status and not cur[1].trusted and bool(cur[1].chain or cur[1].signer_cert))
        self.btn_copy.setEnabled(is_status)
        self.btn_report.setEnabled(bool(st and st.statuses))

    # ----------------------------------------------------------------- azioni
    def _with_status(self, fn) -> None:
        cur = self.current()
        if cur is not None and cur[0] == "status" and self.view is not None:
            fn(cur[1])

    def goto(self) -> None:
        cur = self.current()
        if cur is None or self.view is None:
            return
        obj = cur[1]
        self.c.go_to_field(self.view, obj.page, obj.rect)

    def next_field(self) -> None:
        fields = self.visible_empty_fields()
        if not fields or self.view is None:
            return
        f = fields[self._next_index % len(fields)]
        self._next_index += 1
        for i in range(self.list.count()):
            d = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if d and d[0] == "field" and d[1] is f:
                self.list.setCurrentRow(i)
        self.c.go_to_field(self.view, f.page, f.rect)

    def sign_now(self) -> None:
        fields = self.visible_empty_fields()
        cur = self.current()
        f = cur[1] if cur is not None and cur[0] == "field" else (fields[0] if fields else None)
        if f is not None and self.view is not None:
            self.c.digital_sign(self.view, field=f)

    def export_report(self) -> None:
        if self.view is None:
            return
        st = self.c.state(self.view)
        path, flt = QFileDialog.getSaveFileName(self, _("Esporta rapporto di verifica"), "rapporto-verifica.xml",
                                                _("Rapporto XML (*.xml);;Testo (*.txt)"))
        if not path:
            return
        if path.lower().endswith(".txt") or "txt" in flt and not path.lower().endswith(".xml"):
            data = "\n\n".join(status_details(s) for s in st.statuses)
        else:
            data = report_xml(st.statuses)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
        except OSError as e:
            self.c.ctx.error(_("Esporta rapporto di verifica"), str(e))


TRANSLATIONS_EN = {
    "attestata da marca temporale": "certified by timestamp", "dichiarata dal firmatario": "declared by the signer",
    "Firmatario: {v}": "Signer: {v}", "Ora: {t} ({src})": "Time: {t} ({src})", "Tipo: {v}": "Type: {v}",
    "approvazione": "approval", "certificazione": "certification",
    "marca temporale del documento": "document timestamp",
    "Livello di certificazione: {v}": "Certification level: {v}", "Livello PAdES: {v}": "PAdES level: {v}",
    "Autorità di marca temporale: {v}": "Timestamp authority: {v}", "Motivo": "Reason", "Luogo": "Location",
    "Contatto": "Contact", "Pagina: {v}": "Page: {v}", "Integrità: {v}": "Integrity: {v}",
    "confermata": "confirmed", "non confermata": "not confirmed", "Algoritmo di hash: {v}": "Hash algorithm: {v}",
    "Catena dei certificati:": "Certificate chain:", "emesso da": "issued by", "scade il": "expires",
    "Nessuna firma": "No signatures", "Stato complessivo delle firme": "Overall signature status",
    "Firma ora": "Sign now", "Verifica online": "Verify online",
    "Controlla la revoca dei certificati presso i servizi indicati nei certificati (richiede la rete)":
        "Check certificate revocation with the services listed in the certificates (requires network)", "Campo successivo": "Next field", "Verifica di nuovo": "Verify again",
    "Firme e campi firma": "Signatures and signature fields", "Dettagli della firma": "Signature details",
    "Vai al campo": "Go to field", "Versione firmata": "Signed version",
    "Apre il documento come era al momento della firma": "Opens the document as it was when signed",
    "Considera attendibile": "Trust", "Copia dettagli": "Copy details", "Esporta rapporto…": "Export report…",
    "Nessun documento aperto": "No document open", "Verifica in corso…": "Verifying…",
    "Da firmare: {n}": "To sign: {n}", "Obbligatorio": "Required", "Pagina": "Page",
    "Verifica non riuscita: {e}": "Verification failed: {e}",
    "Verifica delle firme in corso…": "Verifying signatures…",
    "Il documento ha modifiche non salvate: salvandolo, le firme non saranno più valide.":
        "The document has unsaved changes: if saved, the signatures will no longer be valid.",
    "Campo firma vuoto «{n}».": "Empty signature field “{n}”.", "Firma obbligatoria.": "Signature required.",
    "Richiede una marca temporale da {u}.": "Requires a timestamp from {u}.",
    "Esporta rapporto di verifica": "Export verification report",
    "Rapporto XML (*.xml);;Testo (*.txt)": "XML report (*.xml);;Text (*.txt)",
}
