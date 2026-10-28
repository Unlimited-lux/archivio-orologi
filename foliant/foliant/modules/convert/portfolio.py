"""Portfolio PDF (CNV-34), senza interfaccia: file allegati intatti, cartelle, colonne e copertina."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .common import ConversionError, check

SUBTYPES = {"text": "S", "number": "N", "date": "D"}


def _s(text: str) -> str:
    return pymupdf.get_pdf_str(str(text))


def create_portfolio(entries: list[dict], *, title: str = "Portfolio", view: str = "list",
                     columns: list[tuple[str, str, str]] | None = None, sort: tuple[str, bool] | None = None,
                     initial: str | None = None, progress=None) -> tuple[bytes, list[str]]:
    """Crea il portfolio e restituisce (byte, avvisi).

    `entries`: [{"path", "folder" (facoltativa), "name", "description", "values": {chiave: valore}}].
    `columns`: colonne personalizzate [(chiave, etichetta, "text"|"number"|"date")].
    `view`: "list" o "thumbnails". `sort`: (chiave, crescente). `initial`: nome del file da mostrare.
    """
    if not entries:
        raise ConversionError(_("Nessun file da inserire nel portfolio."))
    columns = columns or []
    warnings: list[str] = []
    files = []
    for k, e in enumerate(entries):
        check(progress, 0.6 * k / len(entries), _("Lettura di «{n}»").format(n=Path(e["path"]).name))
        try:
            data = Path(e["path"]).read_bytes()
        except OSError as err:
            warnings.append(_("«{n}» non leggibile, escluso ({e}).").format(n=Path(e["path"]).name, e=err.strerror))
            continue
        if len(data) > 100 * 1024 * 1024:
            warnings.append(_("«{n}» supera 100 MB: il portfolio sarà molto grande.").format(n=Path(e["path"]).name))
        if data[:5] == b"%PDF-" and b"/Encrypt" in data[-4096:]:
            warnings.append(_("«{n}» è protetto: per aprirlo servirà la sua password.").format(n=Path(e["path"]).name))
        files.append((e, data))
    if not files:
        raise ConversionError(_("Nessuno dei file scelti è leggibile."))
    folders = sorted({(e.get("folder") or "").strip() for e, _d in files} - {""})
    folder_id = {name: i + 1 for i, name in enumerate(folders)}
    with ENGINE_LOCK:
        doc = pymupdf.open()
        _cover(doc, title, files)
        used: set[str] = set()
        names: list[str] = []
        specs: list[tuple[str, int]] = []
        for k, (e, data) in enumerate(files):
            check(progress, 0.6 + 0.35 * k / len(files), _("Inserimento dei file…"))
            display = e.get("name") or Path(e["path"]).name
            folder = (e.get("folder") or "").strip()
            base = f"<{folder_id[folder]}>{display}" if folder else display
            key, n = base, 1
            while key.lower() in used:
                n += 1
                stem, ext = os.path.splitext(display)
                key = (f"<{folder_id[folder]}>" if folder else "") + f"{stem} ({n}){ext}"
            used.add(key.lower())
            names.append(key)
            mtime = dt.datetime.fromtimestamp(os.path.getmtime(e["path"])).strftime("D:%Y%m%d%H%M%S")
            stream = doc.get_new_xref()
            doc.update_object(stream, f"<</Type/EmbeddedFile/Params <</Size {len(data)}/ModDate {_s(mtime)}>>>>")
            doc.update_stream(stream, data)           # contenuto identico byte per byte (compresso Flate)
            ci = [f"/{c} {_value(e.get('values', {}).get(c, ''), t)}" for c, _l, t in columns]
            fname = display if folder else Path(key).name
            fs = doc.get_new_xref()
            doc.update_object(fs, f"<</Type/Filespec/F {_s(fname)}/UF {_s(fname)}/Desc {_s(e.get('description') or '')}"
                                  f"/EF <</F {stream} 0 R>>" + (f"/CI <<{' '.join(ci)}>>" if ci else "") + ">>")
            specs.append((key, fs))
        pairs = sorted(specs, key=lambda kv: kv[0].encode("utf-16-be"))
        tree = "[" + " ".join(f"{_s(k)} {x} 0 R" for k, x in pairs) + "]"
        doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</EmbeddedFiles <</Names {tree}>>>>")
        # /Collection
        schema = ["/FileName <</Type/CollectionField/Subtype/F/N " + _s(_("Nome")) + "/O 0/V true>>",
                  "/Description <</Type/CollectionField/Subtype/Desc/N " + _s(_("Descrizione")) + "/O 1/V true>>",
                  "/Size <</Type/CollectionField/Subtype/Size/N " + _s(_("Dimensione")) + "/O 2/V true>>",
                  "/ModDate <</Type/CollectionField/Subtype/ModDate/N " + _s(_("Data di modifica")) + "/O 3/V true>>"]
        for i, (c, label, t) in enumerate(columns):
            schema.append(f"/{c} <</Type/CollectionField/Subtype/{SUBTYPES.get(t, 'S')}/N {_s(label)}/O {4 + i}/V true>>")
        coll = [f"/Type/Collection/Schema <<{' '.join(schema)}>>", f"/View/{'T' if view == 'thumbnails' else 'D'}"]
        if sort:
            coll.append(f"/Sort <</Type/CollectionSort/S/{sort[0]}/A {'true' if sort[1] else 'false'}>>")
        first = initial if initial in names else None
        if first:
            coll.append(f"/D {_s(first)}")
        if folders:
            coll.append(f"/Folders {_folders(doc, folders, folder_id)} 0 R")
        cx = doc.get_new_xref()
        doc.update_object(cx, "<<" + "".join(coll) + ">>")
        doc.xref_set_key(doc.pdf_catalog(), "Collection", f"{cx} 0 R")
        out = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return out, warnings


def _value(v, t: str) -> str:
    if t == "number":
        try:
            return str(float(v)) if v not in ("", None) else "0"
        except (TypeError, ValueError):
            return "0"
    if t == "date" and isinstance(v, (dt.date, dt.datetime)):
        return _s(v.strftime("D:%Y%m%d%H%M%S"))
    return _s(v or "")


def _folders(doc: pymupdf.Document, folders: list[str], folder_id: dict) -> int:
    root = doc.get_new_xref()
    xrefs = [doc.get_new_xref() for _f in folders]
    for i, (name, x) in enumerate(zip(folders, xrefs)):
        nxt = f"/Next {xrefs[i + 1]} 0 R" if i + 1 < len(xrefs) else ""
        doc.update_object(x, f"<</Type/Folder/ID {folder_id[name]}/Name {_s(name)}/Parent {root} 0 R{nxt}>>")
    doc.update_object(root, f"<</Type/Folder/ID 0/Name {_s('')}/Child {xrefs[0]} 0 R>>")
    return root


def _cover(doc: pymupdf.Document, title: str, files) -> None:
    """Copertina leggibile anche dai lettori senza supporto ai portfolio."""
    page = doc.new_page()
    y = 72.0
    page.insert_text((56, y), title, fontsize=20, fontname="helv")
    y += 34
    page.insert_text((56, y), _("Questo PDF è un portfolio: contiene i file seguenti come allegati."),
                     fontsize=10, fontname="helv")
    y += 26
    for e, data in files:
        if y > page.rect.height - 60:
            page = doc.new_page()
            y = 72.0
        folder = (e.get("folder") or "").strip()
        name = (e.get("name") or Path(e["path"]).name)
        line = (f"{folder} / " if folder else "") + name + f"  ({_size(len(data))})"
        page.insert_text((56, y), line, fontsize=11, fontname="helv")
        y += 15
        if e.get("description"):
            page.insert_text((72, y), str(e["description"])[:120], fontsize=9, fontname="helv", color=(0.35, 0.35, 0.35))
            y += 14
        y += 4


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""
