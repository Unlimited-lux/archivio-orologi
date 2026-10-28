"""Controller del modulo Proteggi: azioni, flussi e collegamento con l'interfaccia.

Le finestre di dialogo passano da funzioni `ask_*`/`confirm_*` di questo modulo,
così i test le sostituiscono con monkeypatch.
"""

from __future__ import annotations

import json
import os
import weakref
from contextlib import contextmanager
from pathlib import Path

import pymupdf
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.api import CONTENT, ENGINE_LOCK, SAVED, Context, DocumentError, _

from . import dialogs as D
from . import patterns as PT
from . import policies as POL
from . import protection as P
from . import redaction as R
from . import sanitize as S
from .widgets import Notice, NoticeBoard, PreviewOverlay, RedactAreaTool, RedactionPanel, TextMarkTool

PANEL_ID = "security-redactions"
CODES_KEY = "security/overlay_codes"
STYLE_KEY = "security/style"
def default_codes() -> list[str]:
    """Insieme di codici predefinito, nella lingua dell'interfaccia (NFR-25)."""
    return [_("OMISSIS"), _("[omissis]"), _("Art. 9 GDPR – dato sanitario"), _("Dato personale"),
            _("Pagina omessa")]


# ------------------------------------------------- dialoghi (sostituibili)


def ask_protection(parent, document, save_policy=None, integrity: bool = False) -> D.ProtectDialog | None:
    i = P.info(document)
    current = P.settings_from_document(document)
    current.integrity = integrity
    dlg = D.ProtectDialog(current, already_protected=i.protected,
                          has_open_password=i.has_open_password, parent=parent, save_policy=save_policy)
    return dlg if dlg.exec() == QDialog.DialogCode.Accepted else None


def ask_policy_passwords(parent, policy) -> tuple[str, str] | None:
    dlg = D.PolicyPasswordDialog(policy, parent)
    return dlg.passwords() if dlg.exec() == QDialog.DialogCode.Accepted else None


def ask_envelope_password(parent) -> str | None:
    dlg = D.PasswordPairDialog(_("Password degli allegati"),
                               _("Le pagine restano leggibili da tutti; per aprire o estrarre gli allegati servirà "
                                 "questa password. Nomi e descrizioni degli allegati restano visibili."), parent)
    return dlg.pw.text() if dlg.exec() == QDialog.DialogCode.Accepted else None


def choose_envelope_files(parent) -> list[str]:
    from PySide6.QtWidgets import QFileDialog
    files, _f = QFileDialog.getOpenFileNames(parent, _("File da mettere nella busta"), "", _("Tutti i file (*)"))
    return files


def ask_vault_unsaved(parent, names: list[str]) -> str | None:
    """«save», «discard» o None (annulla) per documenti dello spazio protetto modificati."""
    box = QMessageBox(QMessageBox.Icon.Question, _("Modifiche non salvate"),
                      _("Salvare nello spazio protetto le modifiche a:") + "\n\n" +
                      "\n".join("• " + n for n in names), parent=parent)
    save = box.addButton(_("Salva nello spazio protetto"), QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton(_("Non salvare"), QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.exec()
    clicked = box.clickedButton()
    return "save" if clicked is save else "discard" if clicked is discard else None


def ask_cert_recipients(parent):
    from . import certenc_ui as CU
    dlg = CU.CertEncryptDialog(parent)
    return dlg if dlg.exec() == QDialog.DialogCode.Accepted else None


def ask_cert_credentials(parent, expected: list[str]) -> tuple[str, str] | None:
    from . import certenc_ui as CU
    dlg = CU.CertOpenDialog(parent, expected)
    if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.path.text():
        return None
    return dlg.path.text(), dlg.password.text()


def choose_pdf(parent) -> str | None:
    from PySide6.QtWidgets import QFileDialog
    path, _f = QFileDialog.getOpenFileName(parent, _("Apri con certificato"), "", _("PDF (*.pdf)"))
    return path or None


def choose_vault_files(parent) -> list[str]:
    from PySide6.QtWidgets import QFileDialog
    files, _f = QFileDialog.getOpenFileNames(parent, _("Aggiungi allo spazio protetto"), "", _("PDF (*.pdf)"))
    return files


def ask_new_vault_passphrase(parent) -> str | None:
    dlg = D.PasswordPairDialog(_("Nuova passphrase"), _("Scegli la nuova passphrase dello spazio protetto."), parent)
    return dlg.pw.text() if dlg.exec() == QDialog.DialogCode.Accepted else None


def choose_files(parent) -> tuple[list[str], str | None] | None:
    """File PDF da proteggere e cartella di destinazione (None = accanto agli originali)."""
    from PySide6.QtWidgets import QFileDialog
    files, _f = QFileDialog.getOpenFileNames(parent, _("Scegli i PDF da proteggere"), "", _("PDF (*.pdf)"))
    if not files:
        return None
    out = QFileDialog.getExistingDirectory(parent, _("Cartella per le copie protette"))
    return files, out or None


def ask_password(parent, title: str, text: str) -> str | None:
    return D.ask_password(parent, title, text)


def confirm(parent, title: str, text: str, action: str) -> bool:
    """Conferma per operazioni irreversibili: pulsante distruttivo, fuoco su Annulla."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    ok = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def ask_unapplied(parent, n: int) -> str:
    """«apply», «keep» o «cancel» per il salvataggio con segni non applicati."""
    box = QMessageBox(QMessageBox.Icon.Warning, _("Segni di redazione non applicati"),
                      _("Il documento contiene {n} segni di redazione non applicati: il contenuto sotto i segni "
                        "non è ancora stato rimosso.").format(n=n), parent=parent)
    apply = box.addButton(_("Applica ora"), QMessageBox.ButtonRole.AcceptRole)
    keep = box.addButton(_("Continua con i segni"), QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    return "apply" if clicked is apply else "keep" if clicked is keep else "cancel"


def ask_trust_scope(parent) -> str | None:
    """«document», «folder» o None (annulla)."""
    box = QMessageBox(QMessageBox.Icon.Question, _("Considera attendibile"),
                      _("Gli avvisi sui contenuti attivi non verranno più mostrati per:"), parent=parent)
    doc = box.addButton(_("Questo documento"), QMessageBox.ButtonRole.AcceptRole)
    folder = box.addButton(_("Tutti i documenti di questa cartella"), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    return "document" if clicked is doc else "folder" if clicked is folder else None


def run_dialog(dialog) -> bool:
    return dialog.exec() == QDialog.DialogCode.Accepted


def show_report(dialog) -> bool:
    return dialog.exec() == QDialog.DialogCode.Accepted


class SecurityController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.style = R.MarkStyle()
        self.apply_options = R.ApplyOptions()
        self.preview = PreviewOverlay()
        self.banners: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self.pending_outside: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # doc → [termini]
        self.scans: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._labels: set[str] = set()
        self._indexes: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._mark_pages: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._pending: "weakref.WeakSet" = weakref.WeakSet()
        self.vault_docs: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # doc → id nello spazio
        self.cert_docs: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()    # doc → file d'origine
        self.integrity: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()    # doc → protezione con MAC
        self.integrity_results: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self.panel = RedactionPanel(self)
        self._load_style()

    # ------------------------------------------------- segni: indice e cache
    @contextmanager
    def _change(self, document, label: str, **kw):
        """`document.change()` del modulo: annota l'etichetta (per riconoscere annulla/ripeti
        delle proprie modifiche) e invalida l'indice dei segni."""
        self._labels.add(label)
        try:
            with document.change(label, **kw) as pdf:
                yield pdf
        finally:
            self._forget_marks(document)

    def _forget_marks(self, document) -> None:
        index = self._indexes.get(document)
        if index is not None:
            index.clear()
        self._mark_pages.pop(document, None)

    def marked_pages(self, document) -> dict[int, list[int]]:
        """Pagine → xref dei segni, calcolato una sola volta per versione del documento."""
        if document is None or document.closed:
            return {}
        cached = self._mark_pages.get(document)
        if cached is not None and cached[0] == document.version:
            return cached[1]
        if document.undo_label in self._labels or document.redo_label in self._labels:
            index = self._indexes.get(document)
            if index is not None:
                index.clear()        # annulla/ripeti di una modifica del modulo
        index = self._indexes.setdefault(document, R.RedactIndex())
        with ENGINE_LOCK:
            pages = index.pages(document.pdf)
        self._mark_pages[document] = (document.version, pages)
        return pages

    def has_marks(self, document) -> bool:
        return bool(self.marked_pages(document))

    def marks_of(self, document) -> list[R.Mark]:
        """Segni con i dettagli, caricando solo le pagine che ne hanno."""
        pages = self.marked_pages(document)
        if not pages:
            return []
        with ENGINE_LOCK:
            return R.marks(document.pdf, sorted(pages))

    # ------------------------------------------------------------ utilità
    @property
    def parent(self):
        return self.ctx.window

    def view(self):
        return self.ctx.current_view()

    def document(self):
        v = self.view()
        return v.document if v is not None else None

    def settings(self):
        try:
            return self.ctx.settings()
        except Exception:
            return None

    def _load_style(self) -> None:
        s = self.settings()
        if s is None:
            return
        raw = s.value(STYLE_KEY, "")
        if not raw:
            return
        try:
            d = json.loads(raw)
            self.style = R.MarkStyle(fill=tuple(d.get("fill", (0, 0, 0))), overlay_text=d.get("text", ""),
                                     text_color=tuple(d.get("text_color", (1, 1, 1))),
                                     font_size=float(d.get("size", 0)), repeat_text=bool(d.get("repeat")))
            self.apply_options = R.ApplyOptions(images=d.get("images", R.IMAGES_PIXELS),
                                                graphics=d.get("graphics", R.GRAPHICS_TOUCHED))
        except (ValueError, TypeError):
            pass

    def _save_style(self) -> None:
        s = self.settings()
        if s is None:
            return
        st, op = self.style, self.apply_options
        s.setValue(STYLE_KEY, json.dumps({"fill": list(st.fill), "text": st.overlay_text,
                                          "text_color": list(st.text_color), "size": st.font_size,
                                          "repeat": st.repeat_text, "images": op.images,
                                          "graphics": op.graphics}))

    def codes(self) -> list[str]:
        s = self.settings()
        raw = s.value(CODES_KEY, "") if s is not None else ""
        try:
            codes = json.loads(raw) if raw else default_codes()
        except ValueError:
            codes = default_codes()
        return [str(c) for c in codes if str(c).strip()]

    def set_codes(self, codes: list[str]) -> None:
        s = self.settings()
        if s is not None:
            s.setValue(CODES_KEY, json.dumps([c for c in codes if c.strip()], ensure_ascii=False))

    def export_codes(self, path: str) -> None:
        Path(path).write_text(json.dumps({"codici": self.codes()}, ensure_ascii=False, indent=2), "utf-8")

    def import_codes(self, path: str) -> list[str]:
        try:
            data = json.loads(Path(path).read_text("utf-8"))
            codes = data.get("codici", data) if isinstance(data, dict) else data
            if not isinstance(codes, list):
                raise ValueError
        except (OSError, ValueError) as e:
            raise DocumentError(_("Il file non contiene un insieme di codici valido.")) from e
        self.set_codes([str(c) for c in codes])
        return self.codes()

    def _import_codes_dialog(self) -> list[str] | None:
        paths = self.ctx.ask_open_paths(_("Importa codici"), "JSON (*.json)", multiple=False)
        if not paths:
            return None
        try:
            return self.import_codes(paths[0])
        except DocumentError as e:
            self.ctx.error(_("Importazione non riuscita"), str(e))
            return None

    def _export_codes_dialog(self) -> None:
        path = self.ctx.ask_save_path(_("Esporta codici"), "codici-redazione.json", "JSON (*.json)", ".json")
        if path:
            try:
                self.export_codes(path)
            except OSError as e:
                self.ctx.error(_("Esportazione non riuscita"), str(e))

    def require_modify(self, document) -> bool:
        """Permesso di modifica (SEC-02): con accesso limitato chiede la password dei permessi."""
        if "modify" in P.allowed_now(document):
            return True
        return self.unlock(document)

    def unlock(self, document=None) -> bool:
        document = document or self.document()
        if document is None:
            return False
        if P.owner_known(document):
            return True
        pw = ask_password(self.parent, _("Sblocca"),
                          _("Il documento ha permessi limitati. Inserisci la password dei permessi:"))
        if pw is None:
            return False
        if not P.unlock(document, pw):
            self.ctx.error(_("Password non corretta"), _("La password dei permessi non è corretta."))
            return False
        # il nucleo notifica ACCESS: gli altri moduli (stampa, copia) ricalcolano i comandi
        self.ctx.status(_("Accesso completo"))
        return True

    # ------------------------------------------------------------ protezione
    def protect(self) -> bool:
        doc = self.document()
        if doc is None:
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        warnings = []
        if self._has_signatures(doc):
            warnings.append(_("Il documento contiene firme digitali: la cifratura riscrive il file e le firme "
                              "risulteranno non valide."))
        if _declares_pdfa(doc):
            warnings.append(_("Il documento dichiara la conformità PDF/A o PDF/X: la cifratura la fa decadere "
                              "e la dichiarazione verrà rimossa."))
        if warnings and not confirm(self.parent, _("Proteggere comunque il documento?"), "\n\n".join(warnings),
                                    _("Continua")):
            return False
        dlg = ask_protection(self.parent, doc, save_policy=self.save_policy, integrity=self.has_integrity(doc))
        if dlg is None:
            return False
        settings = dlg.settings()
        current = doc.save_options
        if dlg.keep_open_password:
            settings.open_password = current.get("user_pw", "")
        if dlg.keep_permissions_password:
            settings.permissions_password = current.get("owner_pw", "")
        try:
            P.apply_protection(doc, settings)
        except P.ProtectionError as e:
            self.ctx.error(_("Protezione non applicata"), str(e))
            return False
        self.set_integrity(doc, settings.integrity)
        if _declares_pdfa(doc):
            with self._change(doc, _("Rimuovi dichiarazione PDF/A")) as pdf:
                S.drop_conformance(pdf)
        self.ctx.status(_("Protezione impostata: verrà applicata al salvataggio."))
        self.refresh_document_state(self.view())
        return True

    # ------------------------------- metadati in chiaro e integrità (SEC-06, SEC-11)
    @staticmethod
    def _options_print(opts: dict) -> tuple:
        return (opts.get("encryption"), opts.get("user_pw"), opts.get("owner_pw"), opts.get("permissions"))

    def set_integrity(self, doc, on: bool) -> None:
        if on:
            self.integrity[doc] = self._options_print(doc.save_options)
        else:
            self.integrity.pop(doc, None)

    def has_integrity(self, doc) -> bool:
        """La protezione impostata chiede la verifica di integrità (e non è cambiata dopo)."""
        return self.integrity.get(doc) == self._options_print(doc.save_options)

    def save_strategy(self, doc, path):
        """`Document.save_strategy`: con «Cifra tutto tranne i metadati» o con la verifica
        di integrità il file cifrato è scritto con pyHanko; altrimenti decide il nucleo."""
        from . import crypto as X
        opts = doc.save_options
        if opts.get("encryption") != pymupdf.PDF_ENCRYPT_AES_256:
            return None
        plain_metadata = opts.get("encrypt_metadata", True) is False
        mac = self.has_integrity(doc)
        if not plain_metadata and not mac:
            return None
        plain = doc.to_bytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        if plain_metadata:
            # i metadati in chiaro servono all'indicizzazione: senza XMP se ne crea uno
            # allineato al dizionario Info (che invece resta cifrato)
            tmp = pymupdf.open("pdf", plain)
            try:
                if not (tmp.get_xml_metadata() or "").strip():
                    values = S.read_metadata(tmp)
                    values["custom"] = S.read_custom(tmp)
                    tmp.set_xml_metadata(S.minimal_xmp(values))
                    plain = tmp.tobytes()
            finally:
                tmp.close()
        return X.encrypt(plain, opts.get("user_pw", ""), opts.get("owner_pw", ""), opts.get("permissions", -1),
                         encrypt_metadata=not plain_metadata, mac=mac)

    def check_integrity(self, view) -> str:
        """All'apertura (SEC-11): con un file alterato avviso e sola lettura."""
        from . import crypto as X
        doc = view.document
        data = getattr(doc, "original_bytes", None)
        if not data or not X.has_integrity(data):
            return X.INTEGRITY_NONE
        protection = getattr(doc, "original_protection", {}) or {}
        passwords = [p for p in (getattr(doc, "password", None), protection.get("owner_pw"),
                                 protection.get("user_pw")) if p is not None] + [""]
        result = X.verify(data, passwords)
        self.integrity_results[doc] = result
        board = self.banners.get(view)
        if result == X.INTEGRITY_TAMPERED:
            doc.set_locked(_("Documento cifrato alterato: aperto in sola lettura."))
            if board is not None:
                board.set_notice(Notice("integrity", 0, _("Documento cifrato alterato: la verifica di integrità non "
                                                          "è superata. Il documento è aperto in sola lettura."),
                                        [], kind="danger"))
        elif result == X.INTEGRITY_UNVERIFIABLE and board is not None:
            board.set_notice(Notice("integrity", 1, _("Integrità non verificabile: il codice di autenticazione "
                                                      "manca o non è leggibile."), []))
        return result

    # ------------------------------------------------------ blocco (SEC-32)
    def lock_app(self) -> bool:
        lock = getattr(self, "applock", None)
        if lock is None:
            return False
        if not lock.configured:
            self.lock_settings()
            if not lock.configured:
                return False
        return lock.lock()

    def lock_settings(self) -> bool:
        """Impostazioni del blocco (SEC-32): codice, inattività, blocco all'avvio."""
        from .applock import LockDialog
        lock = getattr(self, "applock", None)
        return lock is not None and run_dialog(LockDialog(lock, self.parent))

    # ------------------------------------------------- busta di sicurezza (SEC-10)
    def save_envelope(self) -> bool:
        """Proteggi › Cifra solo gli allegati: salva una copia in cui solo i file
        incorporati richiedono la password."""
        from . import envelope as E
        doc = self.document()
        if doc is None:
            return False
        with ENGINE_LOCK:
            has = E.has_attachments(doc.pdf)
        if not has:
            self.ctx.info(_("Cifra solo gli allegati"),
                          _("Il documento non ha allegati. Con «Crea busta di sicurezza» puoi preparare un PDF con i "
                            "file da inviare."))
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        pw = ask_envelope_password(self.parent)
        if pw is None:
            return False
        path = self.ctx.ask_save_path(_("Salva busta di sicurezza"), f"{Path(doc.display_name).stem}{_('_busta')}.pdf")
        if not path:
            return False
        with ENGINE_LOCK:
            data = doc.pdf.tobytes(garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        return self._write_envelope(data, pw, path)

    def create_envelope(self, files: list[str] | None = None) -> bool:
        """Proteggi › Crea busta di sicurezza: nuovo PDF con copertina e i file scelti."""
        from . import envelope as E
        files = files if files is not None else choose_envelope_files(self.parent)
        if not files:
            return False
        pw = ask_envelope_password(self.parent)
        if pw is None:
            return False
        path = self.ctx.ask_save_path(_("Salva busta di sicurezza"), _("busta.pdf"))
        if not path:
            return False
        try:
            cover = E.cover_document(files)
            data = cover.tobytes(garbage=1, deflate=True)
            cover.close()
        except OSError as e:
            self.ctx.error(_("Busta non creata"), _("Impossibile leggere il file: {e}").format(e=e.strerror))
            return False
        return self._write_envelope(data, pw, path)

    def _write_envelope(self, data: bytes, password: str, path: str) -> bool:
        from . import envelope as E
        try:
            out = E.encrypt_attachments(data, password)
            with open(path, "wb") as f:
                f.write(out)
        except Exception as e:
            self.ctx.error(_("Busta non creata"), str(e))
            return False
        self.ctx.status(_("Busta di sicurezza salvata: {n}").format(n=os.path.basename(path)))
        return True

    # ------------------------------------------- cifratura con certificato (SEC-09)
    def encrypt_with_certificate(self) -> bool:
        """Proteggi › Cifra con certificato: salva una copia cifrata per i destinatari."""
        from . import certenc as CE
        doc = self.document()
        if doc is None:
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        dlg = ask_cert_recipients(self.parent)
        if dlg is None:
            return False
        warnings = []
        if not any(r.mine for r in dlg.recipients):
            warnings.append(_("Il tuo certificato non è tra i destinatari: non potrai più aprire il documento "
                              "cifrato."))
        if self._has_signatures(doc):
            warnings.append(_("Le firme digitali del documento risulteranno non valide nella copia cifrata."))
        if warnings and not confirm(self.parent, _("Cifrare comunque?"), "\n\n".join(warnings), _("Cifra")):
            return False
        path = self.ctx.ask_save_path(_("Salva copia cifrata"), f"{Path(doc.display_name).stem}{_('_cifrato')}.pdf")
        if not path:
            return False
        with ENGINE_LOCK:
            data = doc.pdf.tobytes(garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        try:
            out = CE.encrypt_for(data, dlg.recipients, dlg.encrypt_metadata.isChecked())
            with open(path, "wb") as f:
                f.write(out)
        except Exception as e:
            self.ctx.error(_("Cifratura non riuscita"), str(e))
            return False
        self.ctx.status(_("Copia cifrata per {n} destinatari salvata: {f}").format(
            n=len(dlg.recipients), f=os.path.basename(path)))
        return True

    def open_with_certificate(self, path: str | None = None, pkcs12: str | None = None,
                              password: str | None = None):
        """Proteggi › Apri con certificato: decifra in memoria con la propria chiave privata."""
        from foliant.core.document import Document
        from . import certenc as CE
        path = path or choose_pdf(self.parent)
        if not path:
            return None
        try:
            data = open(path, "rb").read()
        except OSError as e:
            self.ctx.error(_("Apri con certificato"), _("Impossibile leggere il file: {e}").format(e=e.strerror))
            return None
        if not CE.is_pubkey(data):
            self.ctx.info(_("Apri con certificato"), _("Il documento non è cifrato con certificato: aprilo "
                                                       "normalmente."))
            return None
        if pkcs12 is None:
            chosen = ask_cert_credentials(self.parent, CE.expected_recipients(data))
            if chosen is None:
                return None
            pkcs12, password = chosen
        try:
            res = CE.decrypt_with_pkcs12(data, pkcs12, password or "")
            doc = Document.from_bytes(res.data, display_name=os.path.basename(path), modified=False)
        except Exception as e:
            self.ctx.error(_("Apertura non riuscita"), str(e))
            return None
        self.cert_docs[doc] = path
        view = self.ctx.open_document(doc)
        if not res.full_access:
            self.ctx.status(_("Aperto con i permessi del tuo certificato"))
        return view

    def cert_save_guard(self, view, info: dict) -> bool:
        if view is None or view.document not in self.cert_docs:
            return True
        return confirm(self.parent, _("Salvare senza la cifratura con certificato?"),
                       _("Il documento verrà salvato con una cifratura diversa da quella con certificato (o senza, "
                         "se hai tutti i permessi). Per cifrarlo di nuovo per i destinatari usa «Cifra con "
                         "certificato»."), _("Salva"))

    # ------------------------------------------------- spazio protetto (SEC-31)
    VAULT_FOLDER_KEY = "security/vault/folder"
    VAULT_MINUTES_KEY = "security/vault/minutes"

    def vault(self):
        from . import vault as V
        folder = None
        s = self.settings()
        if s is not None:
            folder = s.value(self.VAULT_FOLDER_KEY, "") or None
        if folder is None:
            from PySide6.QtCore import QStandardPaths
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            folder = os.path.join(base, "spazio-protetto")
        current = getattr(self, "_vault", None)
        if current is None or str(current.folder) != str(folder):
            if current is not None:
                current.lock()
            self._vault = V.Vault(folder)
        return self._vault

    def vault_minutes(self) -> int:
        s = self.settings()
        try:
            return int(s.value(self.VAULT_MINUTES_KEY, 5)) if s is not None else 5
        except (TypeError, ValueError):
            return 5

    def set_vault_minutes(self, minutes: int) -> None:
        s = self.settings()
        if s is not None:
            s.setValue(self.VAULT_MINUTES_KEY, int(minutes))
        self._arm_vault_timer()

    def _arm_vault_timer(self, ms: int | None = None) -> None:
        from .applock import IdleWatcher
        if getattr(self, "vault_idle", None) is None:
            self.vault_idle = IdleWatcher(lambda: self.lock_vault(auto=True), self.ctx.window)
        if self.vault().unlocked:
            self.vault_idle.start(ms if ms is not None else self.vault_minutes() * 60_000)
        else:
            self.vault_idle.stop()

    def manage_vault(self) -> bool:
        """Proteggi › Spazio protetto: crea, sblocca e gestisce i documenti custoditi."""
        from . import vault_ui as VU
        v = self.vault()
        if not v.exists:
            if not run_dialog(VU.SetupDialog(v, self.parent)):
                if v.exists and not v.unlocked:
                    return False
                if not v.exists:
                    return False
        elif not v.unlocked and not self.unlock_vault():
            return False
        self._arm_vault_timer()
        run_dialog(VU.VaultDialog(self, self.parent))
        return True

    def unlock_vault(self) -> bool:
        from . import vault_ui as VU
        v = self.vault()
        dlg = VU.UnlockDialog(v, self.parent)
        if not run_dialog(dlg):
            return False
        if dlg.recovered:
            self.ctx.info(_("Spazio protetto"), _("Hai sbloccato con la chiave di recupero: scegli ora una nuova "
                                                  "passphrase."))
            self.change_vault_passphrase()
        self._arm_vault_timer()
        return v.unlocked

    def change_vault_passphrase(self) -> bool:
        pw = ask_new_vault_passphrase(self.parent)
        if pw is None:
            return False
        try:
            self.vault().set_passphrase(pw)
        except Exception as e:
            self.ctx.error(_("Spazio protetto"), str(e))
            return False
        return True

    def open_from_vault(self, oid: str):
        from foliant.core.document import Document
        from . import vault as V
        v = self.vault()
        try:
            data = v.read(oid)
            doc = Document.from_bytes(data, display_name=v.name(oid))
        except V.VaultTampered as e:
            self.ctx.error(_("Documento alterato"), _("«{n}» non si apre: {e}").format(n=v.name(oid), e=e))
            return None
        except Exception as e:
            self.ctx.error(_("Spazio protetto"), str(e))
            return None
        self.vault_docs[doc] = oid
        return self.ctx.open_document(doc)

    def can_move_to_vault(self) -> bool:
        doc = self.document()
        return doc is not None and doc not in self.vault_docs

    def vault_add_files(self, paths: list[str] | None = None) -> int:
        paths = paths if paths is not None else choose_vault_files(self.parent)
        n = 0
        for path in paths or []:
            try:
                with open(path, "rb") as f:
                    self.vault().add(os.path.basename(path), f.read())
                n += 1
            except OSError as e:
                self.ctx.error(_("Spazio protetto"), _("Impossibile leggere il file: {e}").format(e=e.strerror))
        if n:
            self.ctx.status(_("{n} documenti aggiunti allo spazio protetto").format(n=n))
        return n

    def move_to_vault(self, delete_original: bool | None = None) -> bool:
        """Sposta il documento aperto nello spazio (con le modifiche non salvate) e lo
        riapre da lì; a richiesta elimina il file originale."""
        view = self.view()
        if view is None or view.document in self.vault_docs:
            return False
        doc = view.document
        with ENGINE_LOCK:
            data = doc.save_bytes(None, incremental=False) if doc.path is None else \
                doc.save_bytes(doc.path, incremental=False)
        oid = self.vault().add(doc.display_name, data)
        original = doc.path
        if delete_original is None and original is not None:
            delete_original = confirm(
                self.parent, _("Eliminare il file originale?"),
                _("Il documento è ora nello spazio protetto. Il file originale «{n}» resta sul disco in chiaro: "
                  "eliminarlo? Sulle unità a stato solido la cancellazione sicura non è garantita.")
                .format(n=os.path.basename(str(original))), _("Elimina l'originale"))
        self.ctx.close_view(view, discard=True)
        if delete_original and original is not None:
            try:
                os.unlink(original)
            except OSError as e:
                self.ctx.error(_("Spazio protetto"), _("L'originale non è stato eliminato: {e}").format(e=e.strerror))
        self.open_from_vault(oid)
        return True

    def vault_export(self, oid: str | None, path: str | None = None) -> bool:
        from . import vault as V
        if not oid:
            return False
        v = self.vault()
        if path is None:
            if not confirm(self.parent, _("Esportare una copia in chiaro?"),
                           _("La copia esportata non è cifrata e resta leggibile da chiunque abbia accesso al "
                             "disco."), _("Esporta")):
                return False
            path = self.ctx.ask_save_path(_("Esporta dallo spazio protetto"), v.name(oid))
            if not path:
                return False
        try:
            data = v.read(oid)
            with open(path, "wb") as f:
                f.write(data)
        except (V.VaultError, OSError) as e:
            self.ctx.error(_("Spazio protetto"), str(e))
            return False
        self.ctx.status(_("Copia in chiaro esportata: {n}").format(n=os.path.basename(path)))
        return True

    def vault_remove(self, oid: str | None) -> bool:
        if not oid:
            return False
        v = self.vault()
        if not confirm(self.parent, _("Eliminare dallo spazio protetto?"),
                       _("«{n}» verrà eliminato definitivamente.").format(n=v.name(oid)), _("Elimina")):
            return False
        for doc, other in list(self.vault_docs.items()):
            if other == oid:
                view = next((w for w in self.ctx.views() if w.document is doc), None)
                if view is not None:
                    self.ctx.close_view(view, discard=True)
                self.vault_docs.pop(doc, None)
        v.remove(oid)
        return True

    def _store_in_vault(self, doc) -> bool:
        from foliant.core import document as CD
        oid = self.vault_docs.get(doc)
        if oid is None:
            return False
        with ENGINE_LOCK:
            data = doc.save_bytes(None, incremental=False)
        self.vault().write(oid, data)
        # il documento risulta salvato (CORE_REQUESTS §12: manca un `mark_saved()` pubblico)
        if hasattr(doc, "mark_saved"):
            doc.mark_saved()
        else:
            doc._saved_state = doc._state
            doc._notify(CD.SAVED)
        return True

    def vault_save_guard(self, view, info: dict) -> bool:
        """Salva nello spazio protetto i documenti che vengono da lì: nessuna copia in chiaro
        sul disco, salvo la richiesta esplicita di salvarne una copia."""
        if view is None or view.document not in self.vault_docs:
            return True
        if info.get("copy"):
            return confirm(self.parent, _("Salvare una copia in chiaro?"),
                           _("La copia sarà fuori dallo spazio protetto e non cifrata."), _("Salva la copia"))
        if not self.vault().unlocked:
            self.ctx.error(_("Spazio protetto"), _("Lo spazio protetto è bloccato."))
            return False
        try:
            self._store_in_vault(view.document)
        except Exception as e:
            self.ctx.error(_("Salvataggio non riuscito"), str(e))
            return False
        self.ctx.status(_("Salvato nello spazio protetto: {n}").format(n=view.document.display_name))
        return False                  # il salvataggio su disco del nucleo non serve

    def _vault_unsaved(self, docs) -> list:
        return [d for d in docs if d in self.vault_docs and d.is_modified]

    def _settle_vault_docs(self, docs) -> bool:
        """Modifiche non salvate di documenti dello spazio: salvate lì o scartate, su scelta."""
        docs = self._vault_unsaved(docs)
        if not docs:
            return True
        choice = ask_vault_unsaved(self.parent, [d.display_name for d in docs])
        if choice is None:
            return False
        from foliant.core import document as CD
        for doc in docs:
            if choice == "save":
                try:
                    self._store_in_vault(doc)
                except Exception as e:
                    self.ctx.error(_("Salvataggio non riuscito"), str(e))
                    return False
            else:                                   # scartate: la chiusura non deve chiedere di nuovo
                doc._saved_state = doc._state
                doc._notify(CD.SAVED)
        return True

    def vault_close_guard(self, view, info: dict) -> bool:
        return view is None or self._settle_vault_docs([view.document])

    def vault_quit_guard(self, view, info: dict) -> bool:
        return self._settle_vault_docs([v.document for v in info.get("views", [])])

    def lock_vault(self, auto: bool = False) -> None:
        """Blocca lo spazio: salva al suo interno le modifiche, chiude i documenti e toglie
        la chiave dalla memoria."""
        v = getattr(self, "_vault", None)
        for doc in list(self.vault_docs):
            view = next((w for w in self.ctx.views() if w.document is doc), None)
            try:
                if doc.is_modified and v is not None and v.unlocked:
                    self._store_in_vault(doc)
            except Exception:
                pass
            if view is not None:
                self.ctx.close_view(view, discard=True)
            self.vault_docs.pop(doc, None)
        if v is not None:
            v.lock()
        if getattr(self, "vault_idle", None) is not None:
            self.vault_idle.stop()
        import gc
        gc.collect()
        self.ctx.status(_("Spazio protetto bloccato per inattività") if auto else _("Spazio protetto bloccato"))

    # ------------------------------------------------------------ policy
    def policy_store(self) -> POL.PolicyStore:
        return POL.PolicyStore(self.settings())

    def save_policy(self, name: str, settings: P.ProtectionSettings) -> None:
        self.policy_store().add(POL.Policy.from_settings(name, settings))

    def manage_policies(self) -> bool:
        doc = self.document()
        dlg = D.PolicyDialog(self.policy_store(), doc is not None, self.parent)
        if not run_dialog(dlg):
            return False
        name = dlg.current()
        if dlg.choice == D.PolicyDialog.APPLY:
            return self.apply_policy(name)
        if dlg.choice == D.PolicyDialog.BATCH:
            return bool(self.apply_policy_to_files(name))
        return False

    def apply_policy(self, name: str) -> bool:
        """Applica la policy al documento corrente (le password si chiedono ora)."""
        doc = self.document()
        policy = self.policy_store().get(name)
        if doc is None or policy is None:
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        warnings = []
        if self._has_signatures(doc):
            warnings.append(_("Il documento contiene firme digitali: la cifratura riscrive il file e le firme "
                              "risulteranno non valide."))
        if _declares_pdfa(doc):
            warnings.append(_("Il documento dichiara la conformità PDF/A o PDF/X: la cifratura la fa decadere "
                              "e la dichiarazione verrà rimossa."))
        if warnings and not confirm(self.parent, _("Proteggere comunque il documento?"), "\n\n".join(warnings),
                                    _("Continua")):
            return False
        pw = ask_policy_passwords(self.parent, policy)
        if pw is None:
            return False
        settings = policy.settings(*pw)
        try:
            P.apply_protection(doc, settings)
        except P.ProtectionError as e:
            self.ctx.error(_("Protezione non applicata"), str(e))
            return False
        self.set_integrity(doc, settings.integrity)
        if _declares_pdfa(doc):
            with self._change(doc, _("Rimuovi dichiarazione PDF/A")) as pdf:
                S.drop_conformance(pdf)
        self.ctx.status(_("Policy «{n}» impostata: verrà applicata al salvataggio.").format(n=name))
        self.refresh_document_state(self.view())
        return True

    def apply_policy_to_files(self, name: str, files: list[str] | None = None,
                              out_dir: str | None = None) -> list[tuple[str, str]]:
        """Elaborazione in blocco: salva una copia protetta di ogni file. Restituisce
        [(file, errore)] per quelli non riusciti."""
        policy = self.policy_store().get(name)
        if policy is None:
            return []
        if files is None:
            chosen = choose_files(self.parent)
            if chosen is None:
                return []
            files, out_dir = chosen
        pw = ask_policy_passwords(self.parent, policy)
        if pw is None:
            return []
        settings = policy.settings(*pw)

        def work(progress):
            failed, done = [], 0
            for k, path in enumerate(files):
                progress.update(k / max(1, len(files)), os.path.basename(path))
                try:
                    POL.protect_file(path, POL.protected_name(path, out_dir), settings)
                    done += 1
                except Exception as e:
                    failed.append((path, str(e)))
            return done, failed

        done, failed = self.ctx.run_task(_("Protezione dei file…"), work)
        text = _("{n} file protetti con la policy «{p}».").format(n=done, p=name)
        if failed:
            text += "\n\n" + "\n".join(f"{os.path.basename(f)}: {e}" for f, e in failed)
            self.ctx.error(_("Policy di protezione"), text)
        else:
            self.ctx.status(text)
        return failed

    def fill_policy_menu(self, menu) -> None:
        """Sottomenu Proteggi › Policy: una voce per policy e la gestione."""
        menu.clear()
        store = self.policy_store()
        has_doc = self.document() is not None
        for name in store.names():
            a = menu.addAction(name)
            a.setToolTip(store.get(name).describe())
            a.setEnabled(has_doc)
            a.triggered.connect(lambda _c=False, n=name: self.apply_policy(n))
        if store.names():
            menu.addSeparator()
        menu.addAction(_("Gestisci policy…")).triggered.connect(self.manage_policies)

    def remove_protection(self) -> bool:
        doc = self.document()
        if doc is None:
            return False
        if not doc.is_protected:
            self.ctx.info(_("Rimuovi protezione"), _("Il documento non è protetto."))
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        text = _("Il file salvato sarà leggibile da chiunque, senza password né restrizioni.")
        if self._has_signatures(doc):
            text += "\n\n" + _("Il documento contiene firme digitali: dopo il salvataggio risulteranno non valide.")
        if not confirm(self.parent, _("Rimuovere la protezione?"), text, _("Rimuovi protezione")):
            return False
        P.remove_protection(doc)
        self.ctx.status(_("Protezione rimossa: salva il documento per renderla effettiva."))
        self.refresh_document_state(self.view())
        return True

    def security_properties(self) -> None:
        doc = self.document()
        if doc is None:
            return
        rows = security_rows(doc)
        weak = any(head in (_("Protezione debole"), _("Metodo superato in PDF 2.0")) for head, _d in rows)
        action = (_("Aggiorna ad AES-256") if weak else
                  _("Modifica protezione…") if doc.is_protected else _("Proteggi…"))
        dlg = D.ReportDialog(_("Proprietà di sicurezza"), "", rows, self.parent, action=action)
        if show_report(dlg):
            if weak:
                self.upgrade_to_aes256()
            else:
                self.protect()

    def upgrade_to_aes256(self) -> bool:
        """Mantiene password e permessi, cambia l'algoritmo in AES-256 (richiede accesso completo)."""
        doc = self.document()
        if doc is None:
            return False
        if P.needs_owner_password(doc) and not self.unlock(doc):
            return False
        opts = dict(doc.save_options or doc.original_protection)
        if not opts or opts.get("encryption") in (None, pymupdf.PDF_ENCRYPT_NONE):
            return False
        settings = P.ProtectionSettings(open_password=opts.get("user_pw", ""),
                                        permissions_password=opts.get("owner_pw", ""),
                                        method=P.AES256)
        perms = int(opts.get("permissions", -1))
        settings.restrict = (perms & P.ALL_BITS) != P.ALL_BITS
        settings.allowed = {k for k, bit in P.PERM_BITS.items() if perms & bit}
        settings.print_level = (P.PRINT_NONE if not perms & pymupdf.PDF_PERM_PRINT else
                                P.PRINT_HIGH if perms & pymupdf.PDF_PERM_PRINT_HQ else P.PRINT_LOW)
        if settings.permissions_password == settings.open_password:
            settings.permissions_password = ""
        try:
            P.apply_protection(doc, settings)
        except P.ProtectionError as e:
            self.ctx.error(_("Protezione non applicata"), str(e))
            return False
        self.ctx.status(_("La protezione sarà salvata con AES a 256 bit."))
        self.refresh_document_state(self.view())
        return True

    @staticmethod
    def _has_signatures(doc) -> bool:
        with ENGINE_LOCK:
            try:
                for page in doc.pdf:
                    for w in page.widgets():
                        if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.is_signed:
                            return True
            except Exception:
                return False
        return False

    # ------------------------------------------------------------ redazione
    def area_tool(self) -> RedactAreaTool:
        return RedactAreaTool(self.add_area_mark, self.move_mark)

    def activate_area_tool(self) -> None:
        doc = self.document()
        if doc is None or not self.require_modify(doc):
            return
        self.ctx.set_tool(self.area_tool())
        self.ctx.show_properties(PANEL_ID)

    def add_area_mark(self, page: int, rect: pymupdf.Rect) -> None:
        doc = self.document()
        if doc is None:
            return
        try:
            with self._change(doc, _("Segna area per redazione")) as pdf:
                if R.add_mark(pdf, page, rect, self.style, "area") is None:
                    raise _Nothing()
        except _Nothing:
            return
        self.ctx.status(_("Area segnata: verrà oscurata quando applichi le redazioni."))

    def move_mark(self, page: int, xref: int, rect: pymupdf.Rect) -> None:
        doc = self.document()
        if doc is None:
            return
        try:
            self._move_mark(doc, page, xref, rect)
        except _Nothing:
            pass

    def _move_mark(self, doc, page: int, xref: int, rect: pymupdf.Rect) -> None:
        with self._change(doc, _("Sposta segno di redazione")) as pdf:
            p = pdf[page]
            box = R.page_box(p)
            rect = pymupdf.Rect(rect)
            # resta dentro la pagina
            dx = max(0, box.x0 - rect.x0) - max(0, rect.x1 - box.x1)
            dy = max(0, box.y0 - rect.y0) - max(0, rect.y1 - box.y1)
            rect += (dx, dy, dx, dy)
            annot = p.load_annot(xref)
            if annot is None:
                raise _Nothing()
            annot.set_rect(rect)
            annot.set_info(content=R.covered_text(p, rect))
            annot.update()

    def mark_selection(self) -> int:
        view = self.view()
        if view is None:
            return 0
        sel = view.text_selection
        if sel is None or not sel.rects:
            # nessuna selezione: strumento «Segna testo» (ogni trascinamento crea il segno)
            self.activate_text_tool()
            return 0
        if not self.require_modify(view.document):
            return 0
        with ENGINE_LOCK:
            clipped = PT.clip_to_lines(view.document.pdf[sel.page], [pymupdf.Rect(r) for r in sel.rects])
        rects = merge_line_rects(clipped)
        with self._change(view.document, _("Segna testo per redazione")) as pdf:
            n = R.add_marks(pdf, ((sel.page, r, None) for r in rects), self.style, "selezione")
        view.clear_text_selection()
        self.ctx.show_properties(PANEL_ID)
        return n

    def text_tool(self) -> TextMarkTool:
        return TextMarkTool(self)

    def activate_text_tool(self) -> None:
        doc = self.document()
        if doc is None or not self.require_modify(doc):
            return
        self.ctx.set_tool(self.text_tool())
        self.ctx.show_properties(PANEL_ID)
        self.ctx.status(_("Trascina sul testo da oscurare: il segno copre solo i caratteri scelti."))

    def text_range(self, document, start: tuple[int, pymupdf.Point], end: tuple[int, pymupdf.Point]):
        """Riquadri per pagina dei caratteri tra due punti, anche su più pagine (SEC-14)."""
        (p0, pt0), (p1, pt1) = sorted([start, end], key=lambda t: (t[0], t[1].y, t[1].x))
        out: dict[int, tuple[list[pymupdf.Rect], str]] = {}
        with ENGINE_LOCK:
            pdf = document.pdf
            for page in range(p0, p1 + 1):
                try:
                    text = PT.page_text(pdf[page])
                except Exception:
                    continue
                a = PT.nearest_char(text, pt0) if page == p0 else None
                b = PT.nearest_char(text, pt1) if page == p1 else None
                if page == p0 and a is None:
                    continue
                if p0 == p1 and a is not None and b is not None and a > b:
                    a, b = b, a
                rects, covered = PT.char_range_rects(text, a, b)
                if rects:
                    out[page] = (rects, covered)
        return out

    def mark_text_range(self, document, start: tuple[int, pymupdf.Point], end: tuple[int, pymupdf.Point]) -> int:
        ranges = self.text_range(document, start, end)
        if not ranges:
            return 0
        import secrets
        source = "selezione" if len(ranges) == 1 else f"selezione|{secrets.token_hex(4)}"
        n = 0
        with self._change(document, _("Segna testo per redazione")) as pdf:
            for page, (rects, covered) in ranges.items():
                for r in rects:
                    if R.add_mark(pdf, page, r, self.style, source, covered) is not None:
                        n += 1
        self.ctx.status(_("{n} segni di redazione aggiunti").format(n=n))
        return n

    def convert_highlights(self, document=None, xrefs: set[int] | None = None) -> int:
        """«Converti in segni di redazione»: evidenziazioni (tutte o quelle indicate) → segni."""
        document = document or self.document()
        if document is None or not self.require_modify(document):
            return 0
        n = 0
        with self._change(document, _("Converti evidenziazioni in segni")) as pdf:
            for i, page in R.iter_pages(pdf):
                todo = [x for x, kind, _id in page.annot_xrefs()
                        if kind == pymupdf.PDF_ANNOT_HIGHLIGHT and (xrefs is None or x in xrefs)]
                for x in todo:
                    annot = page.load_annot(x)
                    if annot is None:
                        continue
                    verts = annot.vertices or []
                    quads = [pymupdf.Quad(verts[k:k + 4]) for k in range(0, len(verts) - 3, 4)]
                    rects = [q.rect for q in quads] or [pymupdf.Rect(annot.rect)]
                    page.delete_annot(annot)
                    for r in rects:
                        if R.add_mark(pdf, i, r, self.style, "selezione") is not None:
                            n += 1
        self.ctx.status(_("{n} segni di redazione aggiunti").format(n=n))
        return n

    def mark_matches(self, document, matches: list[PT.Match], source: str) -> int:
        if not matches:
            return 0
        n = 0
        with self._change(document, _("Segna per redazione")) as pdf:
            for m in matches:
                if m.page < 0:
                    continue
                src = f"schema:{m.pattern_label}" if source == "schema" else source
                n += R.add_marks(pdf, ((m.page, r, m.text) for r in m.rects), self.style, src)
        return n

    def search(self, document, options: dict) -> tuple[list[PT.Match], list[str]]:
        """Esegue la ricerca (termini o schemi). Solleva ValueError per input non validi."""
        count = document.page_count
        pages = D.parse_pages(options.get("pages", ""), count)
        pats: list[PT.Pattern] = []
        if "terms" in options:
            terms = options["terms"]
            if not terms:
                raise ValueError(_("Scrivi la parola o la frase da cercare."))
            for t in terms:
                pats.append(PT.term_pattern(t, case_sensitive=options.get("case_sensitive", False),
                                            whole_word=options.get("whole_word", False),
                                            ignore_accents=options.get("ignore_accents", False)))
        else:
            saved = PT.SavedPatterns(self.settings())
            for pid in options.get("patterns", []):
                p = saved.pattern(pid[5:]) if pid.startswith("user:") else PT.pattern_by_id(pid)
                if p is not None:
                    if options.get("include_invalid") and p.check is not None and p.id in ("iban", "cf", "vat",
                                                                                             "card"):
                        from dataclasses import replace
                        p = replace(p, check=None)
                    pats.append(p)
            if options.get("custom"):
                pats.append(PT.custom_pattern(options["custom"]))
            if not pats:
                raise ValueError(_("Scegli almeno uno schema."))

        def work(progress):
            with ENGINE_LOCK:
                found = PT.find(document.pdf, pats, pages, progress.update)
                empty = [i for i in pages if not document.pdf[i].get_text("text").strip()]
                outside = (outside_matches(document.pdf, pats) if options.get("outside") else [])
            return found, empty, outside

        found, empty, outside = self.ctx.run_task(_("Ricerca in corso…"), work)
        notices = []
        if empty:
            notices.append(_("{n} pagine senza testo (scansioni): esegui l'OCR per includerle.").format(n=len(empty)))
        return found + outside, notices

    def find_and_mark(self, mode: str = "terms") -> int:
        view = self.view()
        if view is None or not self.require_modify(view.document):
            return 0
        doc = view.document
        items = [(p.id, p.label, p.id in ("email", "phone", "iban", "cf", "date", "iddoc"))
                 for p in PT.builtin_patterns()]
        dlg = D.FindDialog(mode, lambda o: self.search(doc, o), items, self.parent,
                           saved=PT.SavedPatterns(self.settings()))
        if mode == "terms" and view.selected_text():
            dlg.terms.setPlainText(view.selected_text())
        if not run_dialog(dlg):
            return 0
        chosen = dlg.selected()
        n = self.mark_matches(doc, [m for m in chosen if m.page >= 0],
                              "ricerca" if mode == "terms" else "schema")
        outside = [m for m in chosen if m.page < 0]
        if outside:
            self.pending_outside.setdefault(doc, []).extend(outside)
        self.ctx.status(_("{n} segni di redazione aggiunti").format(n=n + len(outside)))
        self.ctx.show_properties(PANEL_ID)
        return n

    def mark_pages(self) -> int:
        view = self.view()
        if view is None or not self.require_modify(view.document):
            return 0
        dlg = D.PagesDialog(_("Segna pagine per redazione"), view.page_count, view.current_page, self.parent,
                            with_overlay=True, overlay=self.style.overlay_text)
        if not run_dialog(dlg):
            return 0
        pages = dlg.pages()
        if len(pages) == view.page_count and not confirm(
                self.parent, _("Oscurare tutte le pagine?"),
                _("Hai scelto tutte le {n} pagine: dopo l'applicazione non resterà alcun contenuto.").format(
                    n=len(pages)), _("Segna tutte")):
            return 0
        style = R.MarkStyle(**{**self.style.__dict__})
        style.overlay_text = dlg.overlay.text().strip() if dlg.overlay is not None else style.overlay_text
        with self._change(view.document, _("Segna pagine per redazione")) as pdf:
            n = R.mark_whole_pages(pdf, pages, style)
        self.ctx.show_properties(PANEL_ID)
        return n

    def repeat_mark(self, page: int, xref: int) -> int:
        """«Ripeti su più pagine…» per un segno d'area."""
        view = self.view()
        if view is None:
            return 0
        dlg = D.PagesDialog(_("Ripeti su più pagine"), view.page_count, page, self.parent)
        dlg.radios["all"].setChecked(True)
        if not run_dialog(dlg):
            return 0
        return self.repeat_mark_on(view.document, page, xref, dlg.pages())

    def repeat_mark_on(self, document, page: int, xref: int, pages: list[int]) -> int:
        try:
            return self._repeat_mark_on(document, page, xref, pages)
        except _Nothing:
            return 0

    def _repeat_mark_on(self, document, page: int, xref: int, pages: list[int]) -> int:
        with self._change(document, _("Ripeti segno su più pagine")) as pdf:
            src = pdf[page]
            annot = src.load_annot(xref)
            if annot is None:
                raise _Nothing()
            rect = pymupdf.Rect(annot.rect)
            n = 0
            for i in pages:
                if i == page:
                    continue
                if R.add_mark(pdf, i, rect, self.style, "area") is not None:
                    n += 1
        return n

    def remove_marks(self, marks: list[R.Mark]) -> int:
        doc = self.document()
        if doc is None or not marks:
            return 0
        with self._change(doc, _("Rimuovi segni di redazione")) as pdf:
            n = sum(1 for m in marks if R.remove_mark(pdf, m.page, m.xref))
        return n

    def export_marks(self, path: str | None = None) -> bool:
        doc = self.document()
        if doc is None:
            return False
        stem = Path(doc.display_name).stem
        path = path or self.ctx.ask_save_path(_("Esporta segni di redazione"), f"{stem}_redazioni.xfdf",
                                              "XFDF (*.xfdf)", ".xfdf")
        if not path:
            return False
        with ENGINE_LOCK:
            data = R.export_xfdf(doc.pdf, doc.display_name)
        try:
            Path(path).write_text(data, "utf-8")
        except OSError as e:
            self.ctx.error(_("Esportazione non riuscita"), str(e))
            return False
        self.ctx.status(_("Segni esportati in {p}").format(p=path))
        return True

    def import_marks(self, path: str | None = None) -> int:
        doc = self.document()
        if doc is None or not self.require_modify(doc):
            return 0
        if path is None:
            paths = self.ctx.ask_open_paths(_("Importa segni di redazione"), "XFDF (*.xfdf *.xml)", multiple=False)
            if not paths:
                return 0
            path = paths[0]
        try:
            data = Path(path).read_text("utf-8")
            with self._change(doc, _("Importa segni di redazione")) as pdf:
                n = R.import_xfdf(pdf, data, self.style)
        except (OSError, ValueError) as e:
            self.ctx.error(_("Importazione non riuscita"), str(e))
            return 0
        self.ctx.status(_("{n} segni di redazione aggiunti").format(n=n))
        return n

    def restyle_marks(self, marks: list[R.Mark], style: R.MarkStyle | None = None) -> int:
        """Cambia l'aspetto (riempimento, testo sovrapposto) dei segni scelti (SEC-19)."""
        doc = self.document()
        if doc is None or not marks:
            return 0
        if style is None:
            dlg = D.StyleDialog(self.style, self.apply_options, self.codes(), self.parent)
            if not run_dialog(dlg):
                return 0
            style, _options = dlg.values()
        n = 0
        with self._change(doc, _("Modifica aspetto dei segni")) as pdf:
            for m in marks:
                page = pdf[m.page]
                annot = page.load_annot(m.xref)
                if annot is None:
                    continue
                info = dict(annot.info or {})
                rect = pymupdf.Rect(annot.rect)
                page.delete_annot(annot)
                source = info.get("subject", "") or "area"
                if R.add_mark(pdf, m.page, rect, style, source, info.get("content", "")) is not None:
                    n += 1
        return n

    def redaction_settings(self) -> bool:
        dlg = D.StyleDialog(self.style, self.apply_options, self.codes(), self.parent,
                            import_codes=self._import_codes_dialog, export_codes=self._export_codes_dialog)
        if not run_dialog(dlg):
            return False
        self.style, self.apply_options = dlg.values()
        text = self.style.overlay_text
        if text and text not in self.codes():
            self.set_codes(self.codes() + [text])
        self._save_style()
        return True

    def set_preview(self, on: bool) -> None:
        self.preview.enabled = bool(on)
        if self.panel.preview_button.isChecked() != bool(on):
            self.panel.preview_button.setChecked(bool(on))
        for v in self.ctx.views():
            v.viewport().update()

    def apply_redactions(self, marks: list[R.Mark] | None = None) -> bool:
        view = self.view()
        if view is None:
            return False
        doc = view.document
        with ENGINE_LOCK:
            all_marks = R.marks(doc.pdf)
        targets = all_marks if marks is None else marks
        outside = self.pending_outside.get(doc, []) if marks is None else []
        if not targets and not outside:
            self.ctx.info(_("Applica redazioni"), _("Nessuna area contrassegnata per la redazione"))
            return False
        if not self.require_modify(doc):
            return False
        pages = sorted({m.page for m in targets})
        title = _("Applicare {n} redazioni?").format(n=len(targets) + len(outside))
        text = _("Il contenuto sotto {n} aree su {p} pagine verrà rimosso definitivamente (testo, immagini, "
                 "grafica, commenti e campi). Puoi annullare fino al salvataggio; dopo non sarà più "
                 "recuperabile.").format(n=len(targets), p=len(pages))
        if outside:
            text += "\n\n" + _("Verranno cancellate anche {n} occorrenze fuori dalle pagine (metadati, segnalibri, "
                               "commenti, campi, allegati).").format(n=len(outside))
        if self._has_signatures(doc):
            text += "\n\n" + _("Le firme digitali del documento risulteranno non valide.")
        if not confirm(self.parent, title, text, _("Applica")):
            return False
        if not self._apply(view, all_marks, targets, outside):
            return False
        self.offer_save_redacted(view)
        return True

    def _apply(self, view, all_marks: list[R.Mark], targets: list[R.Mark], outside: list) -> bool:
        """Applica i segni scelti (già confermati) in un solo passo annullabile."""
        doc = view.document
        pages = sorted({m.page for m in targets})
        keep = {(m.page, m.xref) for m in all_marks} - {(m.page, m.xref) for m in targets}
        try:
            with self._change(doc, _("Applica redazioni"), kind="redaction") as pdf:
                # i segni non scelti vengono messi da parte e ricreati dopo
                saved = _detach_marks(pdf, keep)
                report = R.apply(pdf, self.apply_options, pages)
                _reattach_marks(pdf, saved)
                if outside:
                    redact_outside(pdf, outside, self.style.overlay_text or "[omissis]")
        except Exception as e:
            self.ctx.error(_("Redazione non applicata"),
                           _("Si è verificato un errore: nessuna modifica è stata fatta.\n\n{e}").format(e=e))
            return False
        if outside:
            self.pending_outside.pop(doc, None)
        self.ctx.status(_("Redazioni applicate: {n} aree su {p} pagine").format(n=report.marks, p=report.pages))
        return True

    def save_guard(self, view, info: dict) -> bool:
        """Prima di salvare (SEC-19): con segni non applicati chiede se applicarli ora."""
        if view is None:
            return True
        doc = view.document
        marks = self.marks_of(doc)
        outside = self.pending_outside.get(doc, [])
        if not marks and not outside:
            return True
        choice = ask_unapplied(self.parent, len(marks) + len(outside))
        if choice == "apply":
            return self.require_modify(doc) and self._apply(view, marks, marks, outside)
        return choice == "keep"

    def offer_save_redacted(self, view) -> None:
        self.offer_save_copy(view, "_redatto", _("Salvare il documento redatto?"))

    def offer_save_copy(self, view, suffix: str, question: str) -> None:
        """Propone «Salva con nome» con il suffisso indicato (es. contratto_ripulito.pdf)."""
        if view is None:
            return
        doc = view.document
        name = f"{Path(doc.display_name).stem}{suffix}.pdf"
        if not self.ctx.confirm(question, _("Salva ora una copia con nome «{n}»: l'originale resta intatto.")
                                .format(n=name)):
            return
        path = self.ctx.ask_save_path(question.rstrip("?"), name)
        if not path:
            return
        try:
            # la copia redatta o ripulita è sempre una riscrittura completa (NFR-09): si usa il
            # salvataggio del nucleo anche se un altro modulo ha sostituito doc.save
            type(doc).save(doc, path, incremental=False)
        except DocumentError as e:
            self.ctx.error(_("Salvataggio non riuscito"), str(e))

    # ------------------------------------------------- ispezione e pulizia
    def inspect(self) -> S.ScanReport | None:
        doc = self.document()
        if doc is None:
            return None

        def work(progress):
            progress.update(0.1, _("Analisi del documento"))
            with ENGINE_LOCK:
                rep, extra = S.scan(doc.pdf), S.inspect_extra(doc.pdf)
            extra["revisions"] = S.previous_revisions(doc.path) if doc.path is not None else 0
            return rep, extra

        rep, extra = self.ctx.run_task(_("Esame del documento…"), work)
        rows = inspection_rows(rep, extra)
        intro = (_("Nessuna informazione nascosta rilevata.") if not rows
                 else _("Il documento contiene queste informazioni nascoste o contenuti attivi:"))
        dlg = D.ReportDialog(_("Esamina documento"), intro, rows, self.parent,
                             action=_("Rimuovi…") if rows else None)
        if show_report(dlg):
            self.sanitize()
        return rep

    def sanitize(self) -> bool:
        doc = self.document()
        if doc is None or not self.require_modify(doc):
            return False
        with ENGINE_LOCK:
            rep = S.scan(doc.pdf)
            extra = S.inspect_extra(doc.pdf)
        counts = {
            "metadata": _("(presenti)") if rep.has_metadata else "",
            "javascript": f"({rep.javascript + rep.launch + rep.submit_forms})" if rep.javascript or rep.launch
            or rep.submit_forms else "",
            "embedded_files": f"({rep.embedded_files})" if rep.embedded_files else "",
            "hidden_layers": f"({rep.hidden_layers})" if rep.hidden_layers else "",
            "comments": f"({rep.comments})" if rep.comments else "",
            "form_data": f"({rep.form_fields})" if rep.form_fields else "",
            "hidden_text": f"({extra['hidden_text']})" if extra.get("hidden_text") else "",
            "links": f"({len(rep.external_links)})" if rep.external_links else "",
        }
        dlg = D.SanitizeDialog(counts, self.parent)
        if not run_dialog(dlg):
            return False
        options = dlg.options()
        if not any(options.get(k) for k in S.SANITIZE_KEYS):
            return False
        try:
            with self._change(doc, _("Rimuovi dati nascosti"), kind="redaction") as pdf:
                result = S.sanitize(pdf, options)
        except Exception as e:
            self.ctx.error(_("Rimozione dei dati nascosti non riuscita"),
                           _("Nessuna modifica è stata fatta.\n\n{e}").format(e=e))
            return False
        labels = S.sanitize_labels()
        done = ", ".join(labels[k].split(" (")[0].lower() for k in result.done if k in labels)
        self.ctx.status(_("Dati nascosti rimossi: {d}").format(d=done or _("nulla da rimuovere")))
        self.refresh_document_state(self.view())
        self.offer_save_copy(self.view(), "_ripulito", _("Salvare il documento ripulito?"))
        return True

    def edit_metadata(self) -> bool:
        doc = self.document()
        if doc is None:
            return False
        with ENGINE_LOCK:
            values = S.read_metadata(doc.pdf)
            values["custom"] = S.read_custom(doc.pdf)
            version = (doc.pdf.metadata or {}).get("format", "")
        read_only = "modify" not in P.allowed_now(doc)
        info = [(_("Applicazione di origine"), values.get("creator", "")),
                (_("Produttore PDF"), values.get("producer", "")),
                (_("Creato il"), S.format_pdf_date(values.get("creationDate", ""))),
                (_("Modificato il"), S.format_pdf_date(values.get("modDate", ""))),
                (_("Versione"), version), (_("Pagine"), str(doc.page_count))]
        dlg = D.MetadataDialog(values, info, read_only, self.parent)
        if not run_dialog(dlg) or read_only:
            return False
        new = dlg.values()
        if all(new[k] == values.get(k, "") for k in new):
            return False
        with self._change(doc, _("Modifica metadati")) as pdf:
            S.write_metadata(pdf, new)
        self.ctx.status(_("Metadati aggiornati"))
        return True

    def flatten(self) -> bool:
        """Proteggi › Appiattisci (SEC-24)."""
        from . import flatten as F
        doc = self.document()
        if doc is None or not self.require_modify(doc):
            return False
        signed = self._has_signatures(doc)
        dlg = D.FlattenDialog(doc.page_count, lambda o: self._flatten_preview(doc, o), signed, self.parent)
        if not run_dialog(dlg):
            return False
        opts = dlg.options()
        if opts.signatures and not confirm(
                self.parent, _("Appiattire le firme digitali?"),
                _("L'aspetto delle firme diventa contenuto statico: le firme non saranno più verificabili."),
                _("Appiattisci le firme")):
            return False
        text = _("Gli elementi scelti diventeranno parte fissa delle pagine. Puoi annullare finché il documento "
                 "è aperto; dopo il salvataggio l'operazione è definitiva.")
        if opts.crop or opts.layers:
            text += "\n\n" + _("Il contenuto fuori dal ritaglio e quello dei livelli nascosti viene eliminato.")
        if signed:
            text += "\n\n" + _("Le firme digitali del documento risulteranno non valide.")
        if not confirm(self.parent, _("Appiattire il documento?"), text, _("Appiattisci")):
            return False
        try:
            with self._change(doc, _("Appiattisci"), kind="redaction") as pdf:
                # ritaglio e rasterizzazione riscrivono il contenuto: i segni di redazione non
                # applicati vengono messi da parte (non devono essere applicati qui) e ricreati
                saved = []
                if opts.crop or opts.rasterize:
                    saved = _detach_marks(pdf, {(p, x) for p in F.pages_with_marks(pdf, opts.pages)
                                                for x, t, _n in pdf[p].annot_xrefs()
                                                if t == pymupdf.PDF_ANNOT_REDACT})
                rep = F.flatten(pdf, opts)
                _reattach_marks(pdf, saved)
        except Exception as e:
            self.ctx.error(_("Appiattimento non eseguito"),
                           _("Si è verificato un errore: nessuna modifica è stata fatta.\n\n{e}").format(e=e))
            return False
        self.ctx.status(_("Documento appiattito"))
        if rep.warnings:
            self.ctx.info(_("Appiattisci"), "\n".join(rep.warnings))
        return True

    def _flatten_preview(self, doc, opts):
        from . import flatten as F
        with ENGINE_LOCK:
            pages = list(range(doc.page_count)) if opts.pages is None else opts.pages
            if not pages:
                return None
            return F.preview(doc.pdf, opts, pages[0], width=480)

    # ----------------------------------------------------- stato e avvisi
    def on_document_opened(self, view) -> None:
        doc = view.document
        if self.preview not in view.overlays:
            view.overlays.append(self.preview)
        if view not in self.banners:
            self.banners[view] = NoticeBoard(self.ctx, view)
        doc.add_listener(self._listener_for(view))
        if getattr(doc, "save_strategy", "absent") is None:
            doc.save_strategy = self.save_strategy
        try:
            self.check_integrity(view)
        except Exception:
            pass
        self.refresh_document_state(view)
        # analisi fuori dal thread dell'interfaccia: il documento è subito utilizzabile e gli
        # avvisi compaiono quando finisce (con FOLIANT_SYNC_TASKS=1 gira subito)
        ref = weakref.ref(view)

        def work(progress):
            return S.scan(doc.pdf)        # prende il lock del motore a blocchi

        def done(rep):
            v = ref()
            if v is None or v.document.closed:
                return
            self.scans[v.document] = rep
            try:
                self.show_open_notices(v, rep)
            finally:
                self.refresh_document_state(v)

        def failed(_exc):
            done(S.ScanReport())

        try:
            self.ctx.run_task(_("Analisi dei contenuti attivi…"), work, modal=False, cancellable=False,
                              on_done=done, on_error=failed)
        except TypeError:                 # nucleo senza attività non modali
            try:
                rep = work(None)
            except Exception:
                rep = S.ScanReport()
            done(rep)

    def _listener_for(self, view):
        ref = weakref.ref(view)

        def listener(doc, kind):
            v = ref()
            if v is None:
                return
            if kind in (CONTENT, "access", SAVED):
                self.schedule_refresh(v)
        self._listeners = getattr(self, "_listeners", weakref.WeakKeyDictionary())
        self._listeners[view] = listener
        return listener

    def schedule_refresh(self, view) -> None:
        """Aggiorna stato e azioni una sola volta per le modifiche ravvicinate (e solo per la
        vista corrente; le altre si aggiornano quando diventano correnti)."""
        if os.environ.get("FOLIANT_SYNC_TASKS") == "1":
            self.refresh_document_state(view)
            return
        first = not self._pending
        self._pending.add(view)
        if first:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._flush_refresh)

    def _flush_refresh(self) -> None:
        views = list(self._pending)
        self._pending.clear()
        for v in views:
            if not v.document.closed:
                self.refresh_document_state(v)

    def banner(self, view) -> NoticeBoard:
        b = self.banners.get(view)
        if b is None:
            b = self.banners[view] = NoticeBoard(self.ctx, view)
        return b

    def show_open_notices(self, view, rep: S.ScanReport) -> None:
        doc = view.document
        banner = self.banner(view)
        if rep.risky and not self.is_trusted(doc):
            parts = []
            if rep.javascript:
                parts.append(_("script ({n})").format(n=rep.javascript))
            if rep.launch:
                parts.append(_("avvio di programmi ({n})").format(n=rep.launch))
            if rep.submit_forms:
                parts.append(_("invio di moduli ({n})").format(n=rep.submit_forms))
            if rep.external_links:
                parts.append(_("collegamenti esterni ({n})").format(n=len(rep.external_links)))
            if rep.remote_files:
                parts.append(_("collegamenti ad altri file ({n})").format(n=rep.remote_files))
            if rep.embedded_files:
                parts.append(_("file incorporati ({n})").format(n=rep.embedded_files))
            text = _("Questo documento contiene {what}.").format(what=", ".join(parts))
            if rep.javascript:
                text += " " + _("Gli script sono disattivati.")
            banner.set_notice(Notice("active", 0, text, [
                (_("Dettagli"), lambda: self.show_active_details(view)),
                (_("Considera attendibile…"), lambda: self.trust_dialog(view)),
            ]))
        n = len(self.marks_of(doc))
        if n:
            banner.set_notice(Notice("unapplied", 1,
                                     _("Il documento contiene {n} segni di redazione non applicati.").format(n=n),
                                     [(_("Rivedi"), lambda: self.show_panel())]))

    def show_active_details(self, view) -> None:
        rep = self.scans.get(view.document) or S.ScanReport()
        rows = active_rows(rep)
        dlg = D.ReportDialog(_("Contenuti attivi"),
                             _("Foliant non esegue script né avvia programmi indicati dai documenti; i collegamenti "
                               "esterni chiedono sempre conferma."), rows, self.parent)
        show_report(dlg)

    def show_panel(self) -> None:
        self.ctx.set_mode("proteggi")
        self.ctx.show_properties(PANEL_ID)

    def refresh_document_state(self, view) -> None:
        if view is None:
            return
        doc = view.document
        banner = self.banners.get(view)
        if banner is not None:
            if not P.owner_known(doc):
                banner.set_notice(Notice("restricted", 0, _("Documento con restrizioni: alcune operazioni non "
                                                            "sono consentite."),
                                         [(_("Sblocca…"), lambda: self.unlock(view.document))]))
            else:
                banner.remove_notice("restricted")
        if view is self.view():
            self.update_status(view)
        for cb in list(getattr(self, "state_callbacks", [])):
            cb(view)

    def update_status(self, view) -> None:
        cb = getattr(self, "status_callback", None)
        if cb is not None:
            cb(view)

    # ---------------------------------------------------------- attendibili
    TRUST_KEY = "security/trusted"

    def _trusted(self) -> dict:
        s = self.settings()
        raw = s.value(self.TRUST_KEY, "") if s is not None else ""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def is_trusted(self, doc) -> bool:
        if doc.path is None:
            return False
        trusted = self._trusted()
        real = str(Path(doc.path).resolve())
        digest = _file_hash(real)
        if trusted.get("files", {}).get(real) == digest and digest:
            return True
        return any(real.startswith(folder.rstrip("/") + "/") for folder in trusted.get("folders", []))

    def trust_document(self, view, folder: bool = False) -> None:
        doc = view.document
        if doc.path is None:
            self.ctx.info(_("Considera attendibile"), _("Salva prima il documento."))
            return
        trusted = self._trusted()
        real = str(Path(doc.path).resolve())
        if folder:
            trusted.setdefault("folders", []).append(str(Path(real).parent))
        else:
            trusted.setdefault("files", {})[real] = _file_hash(real)
        s = self.settings()
        if s is not None:
            s.setValue(self.TRUST_KEY, json.dumps(trusted))
        self.banner(view).remove_notice("active")
        self.ctx.status(_("Documento considerato attendibile"))

    def trust_dialog(self, view) -> None:
        scope = ask_trust_scope(self.parent)
        if scope in ("document", "folder"):
            self.trust_document(view, folder=scope == "folder")

    def revoke_trust(self) -> None:
        s = self.settings()
        if s is not None:
            s.setValue(self.TRUST_KEY, "")


class _Nothing(Exception):
    """Interrompe un blocco di modifica senza effetti (nessun passo di annullamento)."""


def _declares_pdfa(doc) -> bool:
    with ENGINE_LOCK:
        try:
            xmp = doc.pdf.get_xml_metadata() or ""
        except Exception:
            return False
    return "pdfaid:part" in xmp or "pdfxid:" in xmp or "GTS_PDFXVersion" in xmp


def _file_hash(path: str) -> str:
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def merge_line_rects(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Unisce i riquadri delle parole della stessa riga in un solo rettangolo."""
    lines: list[pymupdf.Rect] = []
    for r in sorted(rects, key=lambda r: (round(r.y0), r.x0)):
        for i, line in enumerate(lines):
            overlap = min(line.y1, r.y1) - max(line.y0, r.y0)
            if overlap > 0.5 * min(line.height, r.height):
                lines[i] = line | r
                break
        else:
            lines.append(pymupdf.Rect(r))
    return lines


def _detach_marks(pdf: pymupdf.Document, keep: set[tuple[int, int]]) -> list[tuple[int, pymupdf.Rect, dict]]:
    saved = []
    for page, xref in keep:
        p = pdf[page]
        annot = p.load_annot(xref)
        if annot is None:
            continue
        info = dict(annot.info or {})
        colors = annot.colors or {}
        overlay = ""
        try:
            kind, val = pdf.xref_get_key(xref, "OverlayText")
            overlay = val if kind == "string" else ""
        except Exception:
            pass
        saved.append((page, pymupdf.Rect(annot.rect), {"info": info, "fill": colors.get("fill") or (0, 0, 0),
                                                         "overlay": overlay}))
        p.delete_annot(annot)
    return saved


def _reattach_marks(pdf: pymupdf.Document, saved) -> None:
    for page, rect, data in saved:
        style = R.MarkStyle(fill=tuple(data["fill"]), overlay_text=data["overlay"])
        R.add_mark(pdf, page, rect, style, data["info"].get("subject", "area"), data["info"].get("content", ""))


# ----------------------------------------------- occorrenze fuori pagina


def outside_matches(pdf: pymupdf.Document, pats: list[PT.Pattern]) -> list[PT.Match]:
    """Occorrenze in metadati, segnalibri, commenti, campi e nomi degli allegati."""
    out: list[PT.Match] = []

    def check(where: str, text: str, key: str) -> None:
        if not text:
            return
        for p in pats:
            hay = PT.fold_accents(text) if p.fold else text
            for m in p.compiled().finditer(hay):
                if m.end() > m.start():
                    out.append(PT.Match(-1, m.group(0), [], key, _("Fuori dalle pagine"),
                                        f"{where}: {text[:80]}"))
                    return

    meta = pdf.metadata or {}
    for k in ("title", "author", "subject", "keywords", "creator", "producer"):
        check(_("Metadati ({k})").format(k=k), meta.get(k) or "", f"meta:{k}")
    for n, item in enumerate(pdf.get_toc(simple=False)):
        check(_("Segnalibro"), item[1], f"toc:{n}")
    for i, page in enumerate(pdf):
        for a in page.annots():
            if a.type[0] == pymupdf.PDF_ANNOT_REDACT:
                continue
            check(_("Commento a pagina {n}").format(n=i + 1), (a.info or {}).get("content", ""), f"annot:{i}:{a.xref}")
        for w in page.widgets():
            check(_("Campo «{f}»").format(f=w.field_name), str(w.field_value or ""), f"field:{i}:{w.xref}")
    try:
        for name in pdf.embfile_names():
            info = pdf.embfile_info(name)
            check(_("Allegato"), f"{name} {info.get('filename', '')} {info.get('desc', '')}", f"emb:{name}")
    except Exception:
        pass
    return out


def redact_outside(pdf: pymupdf.Document, matches: list[PT.Match], replacement: str) -> None:
    for m in matches:
        kind, _s, rest = m.pattern.partition(":")
        if kind == "meta":
            meta = dict(pdf.metadata or {})
            meta[rest] = (meta.get(rest) or "").replace(m.text, "")
            pdf.set_metadata({k: v for k, v in meta.items() if k not in ("format", "encryption")})
            try:
                pdf.del_xml_metadata()
            except Exception:
                pass
        elif kind == "toc":
            toc = pdf.get_toc(simple=False)
            n = int(rest)
            if n < len(toc):
                toc[n][1] = toc[n][1].replace(m.text, replacement)
                pdf.set_toc(toc)
        elif kind == "annot":
            page, xref = (int(x) for x in rest.split(":"))
            p = pdf[page]
            annot = p.load_annot(xref)
            if annot is not None:
                annot.set_info(content=(annot.info.get("content", "")).replace(m.text, replacement))
                annot.update()
        elif kind == "field":
            page, xref = (int(x) for x in rest.split(":"))
            S.clear_fields(pdf, pdf[page], {xref})
        elif kind == "emb":
            try:
                pdf.embfile_upd(rest, filename=replacement, desc="")
            except Exception:
                pass


# -------------------------------------------------------------- rapporti


_METHOD_TEXT = {"AES-256": "AES, chiave a 256 bit", "AES-128": "AES, chiave a 128 bit",
                "RC4-128": "RC4, chiave a 128 bit", "RC4-40": "RC4, chiave a 40 bit"}


def CONTROLLER_RESULTS(doc) -> str | None:
    """Esito della verifica di integrità registrato all'apertura (SEC-11)."""
    from . import CONTROLLER
    return CONTROLLER.integrity_results.get(doc) if CONTROLLER is not None else None


def security_rows(doc) -> list[tuple[str, list[str]]]:
    """Righe del riepilogo «Protezione» (SEC-03), dalla cifratura letta dal nucleo."""
    i = P.info(doc)
    enc = P.encryption(doc)
    rows: list[tuple[str, list[str]]] = []
    if not i.protected and not enc:
        rows.append((_("Nessuna protezione"), [_("Tutti i permessi sono consentiti.")]))
    else:
        details = []
        if enc:
            if enc.get("description"):
                details.append(_("All'apertura: {d}").format(d=enc["description"]))
            if enc.get("V") or enc.get("R"):
                details.append(_("Versione {v}, revisione {r}").format(v=enc.get("V", "?"), r=enc.get("R", "?")))
            if not enc.get("encrypt_metadata", True):
                details.append(_("Metadati non cifrati"))
            details.append(_("Password di apertura: {s}").format(
                s=_("sì") if doc.original_protection.get("user_pw") else _("no")))
        details.append(_("Al salvataggio: {d}").format(d=P.describe(doc)))
        head = _("Protezione con password")
        if i.changed and i.method:
            method = {P.AES256: "AES-256", P.AES128: "AES-128", P.RC4: "RC4-128"}[i.method]
            if doc.save_options.get("encryption") == pymupdf.PDF_ENCRYPT_RC4_40:
                method = "RC4-40"
        else:
            method = enc.get("method", "")
        if method in _METHOD_TEXT:
            head += " — " + _(_METHOD_TEXT[method])
        elif method:
            head += " — " + method
        rows.append((head, details))
        from . import crypto as X
        result = CONTROLLER_RESULTS(doc)
        if result == X.INTEGRITY_OK:
            rows.append((_("Verifica di integrità superata"),
                         [_("Il file cifrato non è stato alterato (ISO/TS 32004).")]))
        elif result == X.INTEGRITY_TAMPERED:
            rows.append((_("Documento cifrato alterato"),
                         [_("La verifica di integrità non è superata: il documento è aperto in sola lettura.")]))
        elif result == X.INTEGRITY_UNVERIFIABLE:
            rows.append((_("Integrità non verificabile"),
                         [_("Il codice di autenticazione manca o non è leggibile.")]))
        from .envelope import is_envelope
        with ENGINE_LOCK:
            envelope = is_envelope(doc.pdf)
        if envelope:
            rows.append((_("Busta di sicurezza: solo gli allegati sono cifrati"),
                         [_("Le pagine sono leggibili senza password."),
                          _("Nomi e descrizioni degli allegati restano visibili.")]))
        if enc.get("perms_tampered"):
            rows.append((_("Permessi del documento alterati"),
                         [_("/P e /Perms non coincidono: valgono i permessi più restrittivi.")]))
        weak = method.startswith("RC4") or (method == "AES-256" and enc.get("R") == 5 and not i.changed)
        if not weak and method == "AES-128":
            rows.append((_("Metodo superato in PDF 2.0"),
                         [_("AES a 128 bit è deprecato in PDF 2.0: aggiorna ad AES-256.")]))
        if weak:
            rows.append((_("Protezione debole"),
                         [_("L'algoritmo RC4 o AES-256 revisione 5 non è più considerato sicuro: aggiorna ad "
                            "AES-256.")]))
    rows.append((_("Accesso: completo") if P.owner_known(doc) else _("Accesso: limitato"), []))
    perms = int(doc.save_options.get("permissions", -1)) if i.protected else -1
    if not P.owner_known(doc):
        perms = int(doc.permissions)      # effettivi (con /Perms), dal nucleo
    short = {"modify": _("Modificare il contenuto"), "copy": _("Copiare testo e immagini"),
             "annotate": _("Aggiungere commenti"), "form": _("Compilare i moduli"),
             "accessibility": _("Estrarre il testo per l'accessibilità"),
             "assemble": _("Inserire, ruotare ed eliminare pagine")}
    allowed, denied = [], []
    if not perms & pymupdf.PDF_PERM_PRINT:
        denied.append(_("Stampare"))
    elif not perms & pymupdf.PDF_PERM_PRINT_HQ:
        allowed.append(_("Stampare, solo a bassa risoluzione"))
    else:
        allowed.append(_("Stampare"))
    for key, bit in P.PERM_BITS.items():
        (allowed if perms & bit else denied).append(short[key])
    rows.append((_("Azioni consentite"), allowed))
    rows.append((_("Azioni non consentite"), denied))
    return rows


def active_rows(rep: S.ScanReport) -> list[tuple[str, list[str]]]:
    names = {"javascript": _("Script"), "launch": _("Avvio di programmi o file"), "remote": _("Altri file"),
             "submit": _("Invio di moduli"), "uri": _("Collegamenti esterni")}
    rows: dict[str, list[str]] = {}
    for kind, where, detail in rep.items:
        line = f"{where}: {detail}" if where else detail
        rows.setdefault(names.get(kind, kind), []).append(line)
    if rep.embedded_files:
        rows.setdefault(_("File incorporati"), []).append(str(rep.embedded_files))
    return [(f"{k} ({len(v)})", v) for k, v in rows.items()]


def inspection_rows(rep: S.ScanReport, extra: dict) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    if extra.get("metadata"):
        rows.append((_("Metadati"), extra["metadata"]))
    if extra.get("image_metadata"):
        rows.append((_("Immagini con metadati (EXIF, posizione GPS…): {n}").format(n=extra["image_metadata"]), []))
    rows.extend(active_rows(rep))
    if rep.hidden_layers:
        rows.append((_("Livelli nascosti: {n}").format(n=rep.hidden_layers), extra.get("layers", [])))
    if rep.comments:
        rows.append((_("Commenti: {n}").format(n=rep.comments), []))
    if rep.form_fields:
        rows.append((_("Campi modulo: {n}").format(n=rep.form_fields), extra.get("fields", [])))
    if extra.get("hidden_text"):
        rows.append((_("Testo nascosto: {n} frammenti").format(n=extra["hidden_text"]), extra.get("hidden_where", [])))
    if extra.get("ocr_text"):
        rows.append((_("Livello OCR: {n} pagine").format(n=extra["ocr_text"]), []))
    if extra.get("hidden_annots"):
        rows.append((_("Annotazioni nascoste: {n}").format(n=extra["hidden_annots"]), []))
    if extra.get("thumbnails"):
        rows.append((_("Miniature incorporate: {n}").format(n=extra["thumbnails"]), []))
    if extra.get("bookmarks"):
        rows.append((_("Segnalibri: {n}").format(n=extra["bookmarks"]), []))
    if extra.get("revisions"):
        rows.append((_("Revisioni precedenti nel file: {n}").format(n=extra["revisions"]),
                     [_("Il file contiene versioni precedenti (aggiornamenti incrementali) che possono "
                        "conservare contenuti cancellati. Un salvataggio completo le elimina.")]))
    if extra.get("signatures"):
        rows.append((_("Firme digitali: {n}").format(n=extra["signatures"]), []))
    return rows
