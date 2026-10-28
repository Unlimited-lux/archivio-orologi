"""Comandi del modulo Converti: collegano finestre, motore e documenti aperti."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QMessageBox

from foliant.api import (
    ENGINE_LOCK, Document, FoliantError, OperationCancelled, PasswordRequired, WrongPassword, _,
)

from .common import ConversionError
from .optimize_dialogs import human_size

OFFICE_FILTER_EXT = ("*.docx *.doc *.odt *.rtf *.xlsx *.xls *.ods *.pptx *.ppt *.odp *.vsdx *.vsd *.xps *.oxps "
                     "*.txt *.md *.markdown *.csv *.tsv *.html *.htm")


class ConvertController:
    """Stato e comandi del modulo (uno per finestra)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.last_result = None

    # ============================================================ utilità
    @property
    def window(self):
        return self.ctx.window

    def _run(self, title: str, fn, *args, **kwargs):
        """Esegue in background; restituisce (ok, risultato). Gli errori sono mostrati all'utente."""
        try:
            return True, self.ctx.run_task(title, lambda progress: fn(*args, progress=progress, **kwargs))
        except OperationCancelled:
            self.ctx.status(_("Operazione annullata."))
        except FoliantError as e:
            self.ctx.error(title.rstrip("…. "), str(e))
        except MemoryError:
            self.ctx.error(title.rstrip("…. "), _("Memoria insufficiente per completare l'operazione."))
        except Exception as e:   # mai chiudere l'applicazione per un errore del modulo
            self.ctx.error(title.rstrip("…. "), _("Errore imprevisto: {e}").format(e=e))
        return False, None

    def _ask_password(self, name: str) -> str | None:
        pw, ok = QInputDialog.getText(self.window, _("Documento protetto"),
                                      _("«{name}» è protetto. Inserisci la password:").format(name=name),
                                      QLineEdit.EchoMode.Password)
        return pw if ok else None

    def _document_bytes(self, doc: Document) -> bytes:
        """Stato in memoria del documento (modifiche non salvate comprese), non cifrato."""
        with ENGINE_LOCK:
            return doc.pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)

    def _check_extract_permission(self, doc: Document) -> bool:
        with ENGINE_LOCK:
            encrypted = bool((doc.pdf.metadata or {}).get("encryption")) or bool(doc.original_protection)
            perms = doc.pdf.permissions
        if encrypted and not perms & pymupdf.PDF_PERM_COPY:
            self.ctx.error(_("Conversione non consentita"),
                           _("Il documento non consente l'estrazione del contenuto (permesso «copia del "
                             "contenuto»). Serve la password del proprietario."))
            return False
        return True

    def open_created(self, data: bytes, name: str, modified: bool = True):
        doc = Document.from_bytes(data, display_name=name, modified=modified)
        return self.ctx.open_document(doc)

    # ======================================================= esporta come
    def export(self, fmt: str | None = None):
        """CNV-01: converte il documento attivo (o un file scelto) nel formato desiderato."""
        from .engine import run_conversion, scanned_pages
        from .export_dialog import ExportDialog

        view = self.ctx.current_view()
        password = None
        if view is not None:
            doc = view.document
            if not self._check_extract_permission(doc):
                return None
            source = self._document_bytes(doc)
            name = Path(doc.display_name).stem
            folder = str(doc.path.parent) if doc.path else self.window.last_directory()
            page_count = doc.page_count
            current = view.current_page
        else:
            paths = self.ctx.ask_open_paths(_("Scegli il PDF da convertire"), "PDF (*.pdf)", multiple=False)
            if not paths:
                return None
            source = paths[0]
            name = Path(source).stem
            folder = str(Path(source).parent)
            while True:
                try:
                    with ENGINE_LOCK:
                        from .common import open_source
                        probe = open_source(source, password, need_extract=False)
                        page_count = probe.page_count
                        probe.close()
                    break
                except (PasswordRequired, WrongPassword) as e:
                    if isinstance(e, WrongPassword):
                        QMessageBox.warning(self.window, _("Password errata"), _("La password non è corretta. Riprova."))
                    password = self._ask_password(Path(source).name)
                    if password is None:
                        return None
                except FoliantError as e:
                    self.ctx.error(_("Impossibile aprire il file"), str(e))
                    return None
            current = 0
        try:
            scanned = bool(scanned_pages(source, password))
        except Exception:
            scanned = False
        settings = self.ctx.settings()
        dlg = ExportDialog(self.window, page_count=page_count, current_page=current, name=name, folder=folder,
                           fmt=fmt or settings.value("convert/last_format", "docx") or "docx",
                           settings=settings, scanned=scanned)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        fmt, out_path, options = dlg.values()
        if password:
            options["password"] = password
        from .export_dialog import OCR_FORMATS
        if fmt in OCR_FORMATS and options.get("ocr") == "never" and scanned:
            box = QMessageBox(self.window)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(_("Pagine scansionate"))
            box.setText(_("Il documento contiene pagine scansionate: senza OCR l'uscita conterrà solo immagini."))
            on = box.addButton(_("Attiva OCR"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_("Continua senza OCR"), QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is cancel:
                return None
            if box.clickedButton() is on:
                options["ocr"] = "auto"
        if fmt.startswith("pdfa") and not self._confirm_pdfa(source, fmt, password):
            return None
        ok, result = self._run(_("Conversione in corso…"), run_conversion, source, out_path, fmt, **options)
        if not ok:
            return None
        if fmt.startswith("pdfa"):
            report = result.details.get("pdfa", {})
            if not report.get("compliant", True):
                pass
        self.last_result = result
        self.show_result(result)
        return result

    def _confirm_pdfa(self, source, fmt: str, password: str | None) -> bool:
        """CNV-14: analisi preliminare con l'elenco dei problemi e delle correzioni."""
        try:
            from . import pdfa
            data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
            problems = pdfa.analyze_pdfa(bytes(data), password=password)
        except Exception:
            return True
        if not problems:
            return True
        text = _("Problemi rilevati che verranno corretti:") + "\n\n" + "\n".join(f"• {p}" for p in problems[:15])
        if len(problems) > 15:
            text += "\n" + _("…e altri {n}.").format(n=len(problems) - 15)
        return self.ctx.confirm(_("Conversione in {f}").format(f=fmt.upper().replace("PDFA-", "PDF/A-")), text)

    def show_result(self, result) -> None:
        """Esito con report e azioni «Apri» e «Mostra nella cartella»."""
        if not result.paths:
            return
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Warning if result.warnings else QMessageBox.Icon.Information)
        box.setWindowTitle(_("Conversione completata"))
        if len(result.paths) == 1:
            box.setText(_("File creato: {name}").format(name=Path(result.paths[0]).name))
        else:
            box.setText(_("{n} file creati in {folder}").format(n=len(result.paths), folder=Path(result.paths[0]).parent))
        box.setDetailedText(result.report_text())
        if result.warnings:
            box.setInformativeText(_("Avvisi: {n}. Vedi i dettagli.").format(n=len(result.warnings)))
        open_btn = box.addButton(_("Apri"), QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton(_("Mostra nella cartella"), QMessageBox.ButtonRole.ActionRole)
        save_btn = box.addButton(_("Salva report…"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(_("Chiudi"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        first = result.paths[0]
        if clicked is open_btn:
            if first.lower().endswith(".pdf"):
                self.ctx.open_path(first)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(first))
        elif clicked is folder_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(first).parent)))
        elif clicked is save_btn:
            path = self.ctx.ask_save_path(_("Salva report"), "report_conversione.txt", _("Testo (*.txt)"), ".txt")
            if path:
                Path(path).write_text(result.report_text(), encoding="utf-8")

    # ============================================================== crea
    def create_from_files(self, paths: list[str] | None = None):
        """CNV-18/20/21: PDF da documenti Office, testo, Markdown, CSV, HTML, immagini."""
        from . import create
        from .create_dialogs import IMAGE_FILTER_EXT
        if paths is None:
            paths = self.ctx.ask_open_paths(
                _("Crea PDF da file"),
                _("Documenti supportati ({ext})").format(ext=OFFICE_FILTER_EXT + " " + IMAGE_FILTER_EXT)
                + ";;" + _("Tutti i file (*)"))
        if not paths:
            return []
        views = []
        for path in paths:
            ext = Path(path).suffix.lower()
            if ext == ".pdf":
                v = self.ctx.open_path(path)
                if v:
                    views.append(v)
                continue
            password = None
            while True:
                try:
                    extra = {"password": password} if password else {}
                    data, warnings = self.ctx.run_task(
                        _("Creazione del PDF…"), lambda progress, path=path, extra=extra: create.file_to_pdf(
                            path, progress=progress, **extra))
                    break
                except (PasswordRequired, WrongPassword) as e:
                    if isinstance(e, WrongPassword):
                        QMessageBox.warning(self.window, _("Password errata"), _("La password non è corretta. Riprova."))
                    password = self._ask_password(Path(path).name)
                    if password is None:
                        data = None
                        break
                except OperationCancelled:
                    data = None
                    break
                except FoliantError as e:
                    self.ctx.error(_("Crea PDF da file"), f"{Path(path).name}\n\n{e}")
                    data = None
                    break
                except Exception as e:
                    self.ctx.error(_("Crea PDF da file"), _("Errore imprevisto: {e}").format(e=e))
                    data = None
                    break
            if not data:
                continue
            views.append(self.open_created(data, Path(path).stem + ".pdf"))
            if warnings:
                self.ctx.status("; ".join(warnings[:3]), 10000)
        return views

    def create_from_images(self, paths: list[str] | None = None):
        """CNV-19: PDF da una o più immagini, con ordine, formato pagina e qualità."""
        from . import create
        from .create_dialogs import IMAGE_FILTER_EXT, ImagesDialog

        def ask():
            return self.ctx.ask_open_paths(_("Scegli le immagini"), _("Immagini ({ext})").format(ext=IMAGE_FILTER_EXT))

        if paths is None:
            paths = ask()
            if not paths:
                return None
        dlg = ImagesDialog(self.window, paths, ask_more=ask)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        files = dlg.paths()
        ok, res = self._run(_("Creazione del PDF…"), create.images_to_pdf, files, **dlg.options())
        if not ok:
            return None
        data, warnings = res
        name = (Path(files[0]).stem if len(files) == 1 else _("Immagini")) + ".pdf"
        view = self.open_created(data, name)
        if warnings:
            self.ctx.info(_("Crea PDF da immagini"), "\n".join(warnings[:20]))
        return view

    def create_from_url(self):
        """CNV-21: PDF da una pagina web (URL) o da un file HTML."""
        from . import create
        url, ok = QInputDialog.getText(self.window, _("Crea PDF da pagina web"),
                                       _("Indirizzo della pagina (https://…) o percorso di un file HTML:"))
        if not ok or not url.strip():
            return None
        url = url.strip()

        def work(progress):
            if os.path.exists(url):
                html = Path(url).read_text(encoding="utf-8", errors="replace")
                return create.html_to_pdf(html, base_dir=str(Path(url).parent), progress=progress), Path(url).stem
            if "://" not in url:
                full = "https://" + url
            else:
                full = url
            html, final = create.url_to_html(full)
            progress.update(0.5, _("Impaginazione…"))
            stem = Path(final.rstrip("/").split("/")[-1] or "pagina").stem or "pagina"
            return create.html_to_pdf(html, progress=progress), stem

        ok, res = self._run(_("Caricamento della pagina…"), work)
        if not ok:
            return None
        data, stem = res
        return self.open_created(data, f"{stem}.pdf")

    def clipboard_snapshot(self) -> dict:
        """Istantanea degli appunti nei formati supportati (letta nel thread dell'interfaccia)."""
        cb = QGuiApplication.clipboard()
        mime = cb.mimeData()
        if mime is None:
            return {}
        snap: dict = {}
        if mime.hasFormat("application/pdf"):
            snap["pdf_bytes"] = bytes(mime.data("application/pdf"))
        if mime.hasUrls():
            files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
            if files:
                snap["file_paths"] = files
        if mime.hasImage():
            img = cb.image()
            if not img.isNull():
                buf = QByteArray()
                dev = QBuffer(buf)
                dev.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(dev, "PNG")
                dev.close()
                snap["image_bytes"] = bytes(buf)
        if mime.hasHtml() and mime.html().strip():
            snap["html"] = mime.html()
        if mime.hasText() and mime.text().strip():
            snap["text"] = mime.text()
        return snap

    def clipboard_supported(self) -> bool:
        try:
            return bool(self.clipboard_snapshot())
        except Exception:
            return False

    def create_from_clipboard(self):
        """CNV-22: nuovo PDF dal contenuto degli appunti."""
        from . import create
        snap = self.clipboard_snapshot()
        if not snap:
            self.ctx.info(_("Crea PDF dagli appunti"),
                          _("Gli appunti sono vuoti o contengono un formato non supportato. Copia un'immagine, "
                            "del testo o dei file e riprova."))
            return None
        ok, data = self._run(_("Creazione del PDF…"), create.clipboard_to_pdf, **snap)
        if not ok or not data:
            return None
        return self.open_created(data, _("Appunti") + ".pdf")

    def create_blank(self):
        """CNV-23: nuovo documento con pagine bianche o con stile carta."""
        from . import create
        from .create_dialogs import BlankDialog
        dlg = BlankDialog(self.window)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        ok, data = self._run(_("Creazione del documento…"), lambda progress, **kw: create.blank_pdf(**kw),
                             **dlg.options())
        if not ok:
            return None
        return self.open_created(data, _("Senza titolo") + ".pdf", modified=True)

    # ======================================================== ottimizza
    def _current_source(self):
        """(byte, nome, documento|None, percorso|None) dal documento attivo o da un file scelto."""
        view = self.ctx.current_view()
        if view is not None:
            doc = view.document
            return self._document_bytes(doc), doc.display_name, doc, (str(doc.path) if doc.path else None)
        paths = self.ctx.ask_open_paths(_("Scegli il PDF"), "PDF (*.pdf)", multiple=False)
        if not paths:
            return None
        try:
            data = Path(paths[0]).read_bytes()
        except OSError as e:
            self.ctx.error(_("Impossibile aprire il file"), str(e))
            return None
        return data, Path(paths[0]).name, None, paths[0]

    def compress(self):
        """CNV-29: comprimi con livelli di qualità, stima, anteprima e confronto prima/dopo."""
        from . import optimize
        from .optimize_dialogs import CompressDialog
        src = self._current_source()
        if src is None:
            return None
        data, name, doc, path = src
        password = None
        if doc is None:
            with ENGINE_LOCK:
                try:
                    probe = pymupdf.open(stream=data, filetype="pdf")
                    needs = probe.needs_pass
                    probe.close()
                except Exception:
                    needs = False
            if needs:
                password = self._ask_password(name)
                if password is None:
                    return None
        signed = self._is_signed(data, password)
        if signed and not self.ctx.confirm(_("Documento firmato"),
                                           _("La compressione invaliderà le firme digitali. Continuare?")):
            return None
        ok, estimates = self._run(_("Stima delle dimensioni…"), optimize.estimate_sizes, data, password=password)
        if not ok:
            return None
        page = self.ctx.current_view().current_page if self.ctx.current_view() else 0

        def preview(level):
            ok2, res = self._run(_("Anteprima…"), self._preview_task, data, page, level, password)
            return res if ok2 else None

        before = len(data) if doc is None or not doc.path or doc.is_modified else os.path.getsize(doc.path)
        dlg = CompressDialog(self.window, before=before, estimates=estimates or {}, has_document=doc is not None,
                             preview=preview)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        values = dlg.values()

        def work(progress):
            out = optimize.compress_pdf_bytes(data, values["level"], password=password, progress=progress)
            if values.get("linearize"):
                try:
                    out = optimize.linearize_bytes(out, password=password)
                except FoliantError:
                    pass
            return out

        ok, out = self._run(_("Compressione in corso…"), work)
        if not ok:
            return None
        return self._apply_optimized(out, before, doc, name, path, values["target"], _("Comprimi PDF"),
                                     suffix=_("_compresso"))

    def _preview_task(self, data: bytes, page: int, level: str, password, progress=None):
        from PySide6.QtGui import QImage
        from . import optimize
        with ENGINE_LOCK:
            src = pymupdf.open(stream=data, filetype="pdf")
            if src.needs_pass and password:
                src.authenticate(password)
            page = max(0, min(page, src.page_count - 1))
            one = pymupdf.open()
            one.insert_pdf(src, from_page=page, to_page=page)
            single = one.tobytes()
            src.close()
            one.close()
        comp = optimize.compress_pdf_bytes(single, level, progress=progress)
        images = []
        for blob in (single, comp):
            with ENGINE_LOCK:
                d = pymupdf.open(stream=blob, filetype="pdf")
                r = d[0].rect
                clip = pymupdf.Rect(r.x0 + r.width * 0.3, r.y0 + r.height * 0.3, r.x0 + r.width * 0.6,
                                    r.y0 + r.height * 0.55)
                pix = d[0].get_pixmap(dpi=200, clip=clip)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                d.close()
            images.append(img)
        return tuple(images)

    def _is_signed(self, data: bytes, password) -> bool:
        try:
            with ENGINE_LOCK:
                d = pymupdf.open(stream=data, filetype="pdf")
                if d.needs_pass and password:
                    d.authenticate(password)
                signed = any(w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.field_value
                             for p in d for w in (p.widgets() or []))
                d.close()
                return signed
        except Exception:
            return False

    def _apply_optimized(self, out: bytes, before: int, doc, name: str, path, target: str, title: str,
                         suffix: str):
        after = len(out)
        if after >= before:
            self.ctx.info(title, _("Nessuna riduzione possibile: il file è già ottimizzato ({s}). "
                                   "Il documento non è stato modificato.").format(s=human_size(before)))
            return {"before": before, "after": after, "reduced": False}
        percent = (1 - after / before) * 100
        summary = _("Prima: {a} — Dopo: {b} — Riduzione: {p:.0f}%").format(a=human_size(before), b=human_size(after),
                                                                       p=percent)
        if target == "document" and doc is not None:
            doc.replace_with(out, title)
            self.ctx.status(summary, 10000)
            self.ctx.info(title, summary + "\n\n" + _("Le modifiche si applicano al file quando salvi; "
                                                      "puoi annullarle con Annulla."))
            return {"before": before, "after": after, "reduced": True}
        stem = Path(name).stem
        folder = str(Path(path).parent) if path else self.window.last_directory()
        save = self.ctx.ask_save_path(title, os.path.join(folder, stem + suffix + ".pdf"), "PDF (*.pdf)")
        if not save:
            return None
        if path and os.path.abspath(save) == os.path.abspath(path):
            if not self.ctx.confirm(title, _("Sostituire il file originale?")):
                return None
        from foliant.api import atomic_write
        try:
            atomic_write(save, out)
        except FoliantError as e:
            self.ctx.error(title, str(e))
            return None
        self.ctx.info(title, summary + "\n\n" + _("Salvato in: {p}").format(p=save))
        return {"before": before, "after": after, "reduced": True, "output": save}

    def compress_files(self):
        """CNV-29 in blocco: tabella per file con totale risparmiato."""
        from . import optimize
        paths = self.ctx.ask_open_paths(_("Comprimi più PDF"), "PDF (*.pdf)")
        if not paths:
            return None
        levels = [_("Senza perdita"), _("Alta qualità"), _("Media"), _("Bassa")]
        choice, ok = QInputDialog.getItem(self.window, _("Comprimi più PDF"), _("Livello:"), levels, 2, False)
        if not ok:
            return None
        level = optimize.LEVELS[levels.index(choice)]

        def work(progress):
            rows = []
            for k, p in enumerate(paths):
                progress.update(k / len(paths), Path(p).name)
                out = str(Path(p).with_name(Path(p).stem + _("_compresso") + ".pdf"))
                try:
                    r = optimize.compress_file(p, out, level)
                    rows.append((p, r["before"], r["after"] if r["reduced"] else r["before"],
                                 r.get("output") or _("nessuna riduzione")))
                except OperationCancelled:
                    raise
                except Exception as e:
                    size = os.path.getsize(p) if os.path.exists(p) else 0
                    rows.append((p, size, size, _("errore: {e}").format(e=e)))
            return rows

        ok, rows = self._run(_("Compressione in corso…"), work)
        if not ok:
            return None
        tb, ta = sum(r[1] for r in rows), sum(r[2] for r in rows)
        lines = [f"{Path(p).name}: {human_size(b)} → {human_size(a)} ({note})" for p, b, a, note in rows]
        lines.append(_("Totale: {b} → {a}, risparmiati {s}").format(b=human_size(tb), a=human_size(ta),
                                                                    s=human_size(tb - ta)))
        self.ctx.info(_("Comprimi più PDF"), "\n".join(lines))
        return rows

    def optimize_advanced(self):
        """CNV-30: analisi dello spazio e ottimizzazione dettagliata."""
        from . import optimize
        from .optimize_dialogs import AdvancedDialog
        src = self._current_source()
        if src is None:
            return None
        data, name, doc, path = src
        ok, analysis = self._run(_("Analisi dello spazio…"), lambda progress: optimize.analyze_space(data))
        if not ok:
            return None
        dlg = AdvancedDialog(self.window, analysis=analysis, has_document=doc is not None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        values = dlg.values()
        target = dlg.target.currentData()
        ok, out = self._run(_("Ottimizzazione in corso…"), lambda progress: optimize.optimize_advanced(
            data, progress=progress, **values))
        if not ok:
            return None
        return self._apply_optimized(out, len(data), doc, name, path, target, _("Ottimizzazione avanzata"),
                                     suffix=_("_ottimizzato"))

    def repair(self):
        """CNV-32: ripara uno o più PDF danneggiati salvando una copia «_riparato»."""
        from . import optimize
        paths = self.ctx.ask_open_paths(_("Ripara PDF"), _("PDF (*.pdf);;Tutti i file (*)"))
        if not paths:
            return None
        results = []
        for p in paths:
            try:
                out, report = self.ctx.run_task(_("Riparazione in corso…"), lambda progress, p=p:
                                                optimize.repair_file(p, None, progress=progress))
            except OperationCancelled:
                break
            except ConversionError as e:
                detected = getattr(e, "detected_type", None)
                if detected and detected not in ("pdf", "unknown") and self.ctx.confirm(
                        _("Ripara PDF"), f"{e}\n\n" + _("Creare un PDF da questo file?")):
                    if detected in ("jpeg", "png", "tiff", "gif", "bmp", "webp"):
                        tmp_copy = self._copy_with_extension(p, detected)
                        self.create_from_images([tmp_copy] if tmp_copy else [p])
                    else:
                        self.create_from_files([p])
                elif not detected or detected in ("pdf", "unknown"):
                    self.ctx.error(_("Ripara PDF"), f"{Path(p).name}\n\n{e}")
                continue
            except FoliantError as e:
                self.ctx.error(_("Ripara PDF"), f"{Path(p).name}\n\n{e}")
                continue
            except Exception as e:
                self.ctx.error(_("Ripara PDF"), _("Errore imprevisto: {e}").format(e=e))
                continue
            results.append((p, out, report))
            lines = [_("Copia riparata: {p}").format(p=out),
                     _("Pagine recuperate: {n}").format(n=report.get("pages", 0))]
            if report.get("lost_pages"):
                lines.append(_("Pagine perse: {p}").format(p=", ".join(map(str, report["lost_pages"]))))
            lines.extend(report.get("warnings", [])[:10])
            box = QMessageBox(self.window)
            box.setWindowTitle(_("Ripara PDF"))
            box.setText("\n".join(lines))
            open_btn = box.addButton(_("Apri"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_("Chiudi"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self.ctx.open_path(out)
        return results

    @staticmethod
    def _copy_with_extension(path: str, kind: str) -> str | None:
        import shutil
        import tempfile
        ext = {"jpeg": ".jpg"}.get(kind, "." + kind)
        try:
            fd, tmp = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            shutil.copyfile(path, tmp)
            return tmp
        except OSError:
            return None

    # ============================================================ in blocco
    def batch_convert(self):
        """CNV-17: converte molti PDF verso un formato, con report finale."""
        from .batch_dialog import BatchDialog, show_batch_report
        dlg = BatchDialog(self.window, self.ctx)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        from .batch import batch_convert
        files, fmt, out_dir, keep, root, options = dlg.values()
        ok, items = self._run(_("Conversione in blocco…"), batch_convert, files, fmt, out_dir=out_dir,
                              keep_structure=keep, root=root, **options)
        if not ok:
            return None
        show_batch_report(self, items)
        return items

    def batch_create(self):
        """CNV-28: crea PDF da molti file (un PDF per file o un unico PDF)."""
        from . import create
        paths = self.ctx.ask_open_paths(_("Crea PDF in blocco"), _("Tutti i file (*)"))
        if not paths:
            return None
        modes = [_("Un PDF per file"), _("Un unico PDF")]
        choice, ok = QInputDialog.getItem(self.window, _("Crea PDF in blocco"), _("Risultato:"), modes, 0, False)
        if not ok:
            return None
        single = modes.index(choice) == 1
        out_dir = None
        if not single:
            out_dir = self.ctx.ask_directory(_("Cartella di destinazione"))
            if not out_dir:
                return None
        ok, report = self._run(_("Creazione dei PDF…"), lambda progress: create.batch_create(
            paths, mode="single" if single else "separate", output_dir=out_dir, progress=progress))
        if not ok:
            return None
        if single and report.get("data"):
            self.open_created(report["data"], _("Documenti uniti") + ".pdf")
        lines = [_("PDF creati: {n}").format(n=len(report.get("created", [])) or (1 if single else 0))]
        for p, reason in report.get("skipped", []):
            lines.append(_("Saltato: {f} ({r})").format(f=Path(p).name, r=reason))
        self.ctx.info(_("Crea PDF in blocco"), "\n".join(lines))
        return report
