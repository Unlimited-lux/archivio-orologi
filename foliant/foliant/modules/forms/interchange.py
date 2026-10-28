"""Formati di interscambio dei dati dei moduli: FDF, XFDF, CSV e JSON.

Lavorano su dizionari «nome completo del campo -> valore» (vedi
`engine.form_values`): testo, nome di stato («Yes», «Off»…) o lista di testi.
FDF e XFDF sono scritti e letti in proprio secondo lo standard pubblico
(FDF: stessa sintassi degli oggetti PDF; XFDF: XML nello spazio dei nomi
http://ns.adobe.com/xfdf/).
"""

from __future__ import annotations

import csv
import io
import json
import os
import xml.etree.ElementTree as ET
from typing import Any

from foliant.i18n import _

from . import pdfsyntax as ps

XFDF_NS = "http://ns.adobe.com/xfdf/"

FORMATS = {
    "fdf": "FDF (*.fdf)",
    "xfdf": "XFDF (*.xfdf)",
    "csv": "CSV (*.csv)",
    "json": "JSON (*.json)",
    "xml": "XML (*.xml)",
    "txt": "TXT (*.txt)",
}


class InterchangeError(Exception):
    """File di dati non valido (messaggio per l'utente)."""


def _tree(values: dict[str, Any]) -> dict:
    """Albero dei nomi gerarchici: «a.b» -> {"a": {"b": valore}}. Le foglie hanno la chiave None."""
    root: dict = {}
    for name, value in values.items():
        node = root
        parts = name.split(".")
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                child = node[part] = {None: child}
            node = child
        leaf = parts[-1]
        if isinstance(node.get(leaf), dict):
            node[leaf][None] = value
        else:
            node[leaf] = value
    return root


# ====================================================================== FDF
def _fdf_value(value: Any, names: set[str] | None, name: str) -> Any:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    if isinstance(value, bool):
        return ps.Name("Yes" if value else "Off")
    if names is not None and name in names and isinstance(value, str) and value:
        return ps.Name(value)
    return "" if value is None else str(value)


def _fdf_fields(tree: dict, prefix: str, button_names: set[str] | None) -> list[dict]:
    out = []
    for key, value in tree.items():
        if key is None:
            continue
        full = f"{prefix}.{key}" if prefix else key
        entry: dict = {"T": key}
        if isinstance(value, dict):
            if None in value:
                entry["V"] = _fdf_value(value[None], button_names, full)
            entry["Kids"] = _fdf_fields(value, full, button_names)
        else:
            entry["V"] = _fdf_value(value, button_names, full)
        out.append(entry)
    return out


def export_fdf(values: dict[str, Any], pdf_name: str = "", button_names: set[str] | None = None) -> bytes:
    """FDF con i valori. `button_names`: campi i cui valori sono nomi di stato (caselle, opzioni)."""
    if button_names is None:
        button_names = {n for n, v in values.items() if v == "Off" or isinstance(v, bool)}
    fdf: dict = {"Fields": _fdf_fields(_tree(values), "", button_names)}
    if pdf_name:
        fdf["F"] = os.path.basename(pdf_name)
    body = ps.serialize({"FDF": fdf})
    return ("%FDF-1.2\n%\xe2\xe3\xcf\xd3\n1 0 obj\n" + body + "\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n").encode("latin-1")


def _deref(value, objects: dict):
    seen = 0
    while isinstance(value, ps.Ref) and seen < 32:
        value = objects.get(value[0])
        seen += 1
    return value


def _fdf_plain(value, objects) -> Any:
    value = _deref(value, objects)
    if isinstance(value, list):
        return [ps.text_of(_deref(v, objects)) or "" for v in value]
    if isinstance(value, ps.Name):
        return str(value)
    if isinstance(value, dict):  # flusso di testo ricco: non supportato
        return None
    return ps.text_of(value)


def parse_fdf(data: bytes) -> dict[str, Any]:
    """Valori contenuti in un file FDF."""
    if not data.lstrip().startswith(b"%FDF"):
        raise InterchangeError(_("Il file non è in formato FDF."))
    p = ps.parser(data)
    objects: dict[int, Any] = {}
    roots: list = []
    try:
        while not p.eof():
            v = p.value()
            if isinstance(v, int) and not isinstance(v, bool):
                gen = p.value()
                kw = p.value()
                if isinstance(gen, int) and kw == ps.Name("obj"):
                    obj = p.value()
                    if p.peek_keyword(b"stream"):
                        obj = None  # i flussi non contengono valori di campo
                        end = data.find(b"endstream", p.i)
                        p.i = len(data) if end < 0 else end + len(b"endstream")
                    if p.peek_keyword(b"endobj"):
                        p.value()
                    objects[v] = obj
            elif isinstance(v, dict):
                roots.append(v)
    except ps.PdfSyntaxError as e:
        raise InterchangeError(_("Il file FDF è danneggiato ({e}).").format(e=e)) from e
    fdf = None
    for trailer in roots:
        root = _deref(trailer.get("Root"), objects)
        if isinstance(root, dict) and isinstance(_deref(root.get("FDF"), objects), dict):
            fdf = _deref(root["FDF"], objects)
            break
    if fdf is None:
        for obj in objects.values():
            if isinstance(obj, dict) and isinstance(_deref(obj.get("FDF"), objects), dict):
                fdf = _deref(obj["FDF"], objects)
                break
    if fdf is None:
        raise InterchangeError(_("Il file FDF non contiene dati di modulo."))
    out: dict[str, Any] = {}

    def walk(fields, prefix):
        for f in _deref(fields, objects) or []:
            f = _deref(f, objects)
            if not isinstance(f, dict):
                continue
            t = ps.text_of(_deref(f.get("T"), objects))
            full = prefix if t is None else (f"{prefix}.{t}" if prefix else t)
            if "V" in f and full:
                v = _fdf_plain(f["V"], objects)
                if v is not None:
                    out[full] = v
            if "Kids" in f:
                walk(f["Kids"], full)

    walk(fdf.get("Fields"), "")
    return out


# ====================================================================== XFDF
def export_xfdf(values: dict[str, Any], pdf_name: str = "") -> bytes:
    ET.register_namespace("", XFDF_NS)
    root = ET.Element(f"{{{XFDF_NS}}}xfdf", {"xml:space": "preserve"})
    if pdf_name:
        ET.SubElement(root, f"{{{XFDF_NS}}}f", {"href": os.path.basename(pdf_name)})
    fields = ET.SubElement(root, f"{{{XFDF_NS}}}fields")

    def add(parent, tree):
        for key, value in tree.items():
            if key is None:
                continue
            el = ET.SubElement(parent, f"{{{XFDF_NS}}}field", {"name": key})
            if isinstance(value, dict):
                if None in value:
                    _xfdf_values(el, value[None])
                add(el, value)
            else:
                _xfdf_values(el, value)

    add(fields, _tree(values))
    ET.indent(root)
    body = ET.tostring(root, encoding="utf-8").replace(b"\r", b"&#13;")  # CR conservato come riferimento
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _xfdf_values(el, value) -> None:
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        v = ET.SubElement(el, f"{{{XFDF_NS}}}value")
        v.text = "Yes" if item is True else ("Off" if item is False else ("" if item is None else str(item)))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xfdf(data: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise InterchangeError(_("Il file XFDF non è un XML valido ({e}).").format(e=e)) from e
    if _local(root.tag) != "xfdf":
        raise InterchangeError(_("Il file non è in formato XFDF."))
    out: dict[str, Any] = {}

    def walk(el, prefix):
        for child in el:
            if _local(child.tag) != "field":
                continue
            name = child.get("name", "")
            full = f"{prefix}.{name}" if prefix and name else (name or prefix)
            vals = [(v.text or "") for v in child if _local(v.tag) in ("value", "value-richtext")]
            if vals and full:
                out[full] = vals[0] if len(vals) == 1 else vals
            walk(child, full)

    for fields in root:
        if _local(fields.tag) == "fields":
            walk(fields, "")
    return out


# ====================================================================== CSV
def export_csv(values: dict[str, Any] | list[dict[str, Any]], delimiter: str = ",") -> bytes:
    """Una riga di intestazione con i nomi e una riga di valori per ogni record."""
    rows = values if isinstance(values, list) else [values]
    names: list[str] = []
    for r in rows:
        for n in r:
            if n not in names:
                names.append(n)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(names)
    for r in rows:
        w.writerow(["; ".join(map(str, v)) if isinstance(v, (list, tuple)) else ("" if r.get(n) is None else str(v))
                    for n, v in ((n, r.get(n, "")) for n in names)])
    return buf.getvalue().encode("utf-8")


def parse_csv(data: bytes, row: int = 0) -> dict[str, Any]:
    """Valori della riga `row` (0 = prima riga dopo l'intestazione)."""
    rows = csv_records(data)
    if not rows:
        raise InterchangeError(_("Il file CSV non contiene righe di dati."))
    if not 0 <= row < len(rows):
        raise InterchangeError(_("Il file CSV ha solo {n} righe di dati.").format(n=len(rows)))
    return rows[row]


def csv_records(data: bytes) -> list[dict[str, Any]]:
    text = _decode(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        raise InterchangeError(_("Il file CSV è vuoto."))
    header = [h.strip() for h in header]
    if not any(header):
        raise InterchangeError(_("La prima riga del file CSV deve contenere i nomi dei campi."))
    out = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        out.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h})
    return out


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# ====================================================================== JSON
def export_json(values: dict[str, Any]) -> bytes:
    return json.dumps(values, ensure_ascii=False, indent=2).encode("utf-8")


def parse_json(data: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(_decode(data))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InterchangeError(_("Il file JSON non è valido ({e}).").format(e=e)) from e
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        obj = obj[0]
    if not isinstance(obj, dict):
        raise InterchangeError(_("Il file JSON deve contenere un oggetto «nome del campo: valore»."))
    if isinstance(obj.get("fields"), dict) and len(obj) <= 2:
        obj = obj["fields"]
    out: dict[str, Any] = {}

    def flat(d: dict, prefix: str) -> None:
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                flat(v, full)
            elif isinstance(v, list):
                out[full] = [str(x) for x in v]
            elif isinstance(v, bool):
                out[full] = v
            elif v is None:
                out[full] = ""
            elif isinstance(v, float) and v.is_integer():
                out[full] = str(int(v))
            else:
                out[full] = str(v)

    flat(obj, "")
    return out


# ====================================================================== XML semplice e TXT
def export_xml(values: dict[str, Any]) -> bytes:
    """XML semplice: <fields><field name="..."><value>...</value></field></fields>."""
    root = ET.Element("fields")
    for name, value in values.items():
        el = ET.SubElement(root, "field", {"name": name})
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            v = ET.SubElement(el, "value")
            v.text = "" if item is None else str(item)
    ET.indent(root)
    body = ET.tostring(root, encoding="utf-8").replace(b"\r", b"&#13;")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body


def parse_xml(data: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        line, col = e.position
        raise InterchangeError(_("Il file XML non è ben formato (riga {l}, colonna {c}).").format(l=line, c=col + 1)) from e
    out: dict[str, Any] = {}
    for el in root.iter():
        if _local(el.tag) != "field" or not el.get("name"):
            continue
        vals = [(v.text or "") for v in el if _local(v.tag) == "value"]
        if not vals and not len(el):
            vals = [el.text or ""]
        out[el.get("name")] = vals[0] if len(vals) == 1 else vals
    if not out:
        raise InterchangeError(_("Il file XML non contiene campi («field» con attributo «name»)."))
    return out


# ====================================================================== scelta del formato
def format_of(path: str, data: bytes | None = None) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in FORMATS:
        return ext
    if data is not None:
        head = data.lstrip()[:200]
        if head.startswith(b"%FDF"):
            return "fdf"
        if head.startswith(b"<") and b"xfdf" in data[:1000]:
            return "xfdf"
        if head.startswith((b"{", b"[")):
            return "json"
    return "csv"


def export_data(fmt: str, values: dict[str, Any], pdf_name: str = "", button_names: set[str] | None = None) -> bytes:
    if fmt == "fdf":
        return export_fdf(values, pdf_name, button_names)
    if fmt == "xfdf":
        return export_xfdf(values, pdf_name)
    if fmt == "csv":
        return export_csv(values)
    if fmt == "json":
        return export_json(values)
    if fmt == "xml":
        return export_xml(values)
    if fmt == "txt":
        return export_csv(values, delimiter="\t")
    raise InterchangeError(_("Formato non supportato: {f}").format(f=fmt))


def parse_data(fmt: str, data: bytes) -> dict[str, Any]:
    if fmt == "fdf":
        return parse_fdf(data)
    if fmt == "xfdf":
        return parse_xfdf(data)
    if fmt == "csv":
        return parse_csv(data)
    if fmt == "json":
        return parse_json(data)
    if fmt == "xml":
        return parse_xml(data)
    if fmt == "txt":
        return parse_csv(data)
    raise InterchangeError(_("Formato non supportato: {f}").format(f=fmt))
