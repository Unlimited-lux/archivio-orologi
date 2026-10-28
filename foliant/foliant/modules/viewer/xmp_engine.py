"""Metadati XMP avanzati (VIS-41): logica pura, senza interfaccia.

Il pacchetto XMP (ISO 16684-1, RDF/XML in un involucro `xpacket`) si legge come elenco di
proprietà (schema, nome, valore): semplici, sequenze/insiemi (`rdf:Seq`, `rdf:Bag`), testi per
lingua (`rdf:Alt`, si usa `x-default`) oppure strutture complesse conservate così come sono
(per esempio le descrizioni degli schemi di estensione PDF/A). Il pacchetto si riscrive in una
sola `rdf:Description`. Le funzioni che ricevono `pdf` vanno chiamate con ENGINE_LOCK (o
dentro `Document.change()`)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import pymupdf

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
X = "adobe:ns:meta/"
XML_NS = "http://www.w3.org/XML/1998/namespace"
SCHEMAS = {   # prefisso -> URI degli schemi noti
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "pdf": "http://ns.adobe.com/pdf/1.3/",
    "pdfaid": "http://www.aiim.org/pdfa/ns/id/",
    "pdfaExtension": "http://www.aiim.org/pdfa/ns/extension/",
    "pdfaSchema": "http://www.aiim.org/pdfa/ns/schema#",
    "pdfaProperty": "http://www.aiim.org/pdfa/ns/property#",
    "foliant": "http://ns.foliant.app/custom/1.0/",
}
CUSTOM_PREFIX = "foliant"
SIMPLE, SEQ, BAG, ALT, RAW = "simple", "seq", "bag", "alt", "raw"
# proprietà Dublin Core e xmpRights con il loro tipo
KNOWN_KINDS = {("dc", "creator"): SEQ, ("dc", "title"): ALT, ("dc", "description"): ALT, ("dc", "rights"): ALT,
               ("dc", "subject"): BAG, ("dc", "contributor"): BAG, ("dc", "publisher"): BAG,
               ("dc", "language"): BAG, ("dc", "date"): SEQ, ("xmpRights", "UsageTerms"): ALT,
               ("xmpRights", "Owner"): BAG}

for _p, _u in SCHEMAS.items():
    ET.register_namespace(_p, _u)
ET.register_namespace("rdf", RDF)
ET.register_namespace("x", X)


@dataclass
class Prop:
    prefix: str                         # prefisso dello schema (come in SCHEMAS o del documento)
    name: str
    value: str | list[str] = ""         # testo, oppure elenco per SEQ/BAG
    kind: str = SIMPLE
    uri: str = ""                       # URI dello schema
    raw: str = ""                       # XML della proprietà (RAW)

    @property
    def key(self) -> tuple[str, str]:
        return (self.uri or SCHEMAS.get(self.prefix, ""), self.name)

    def text(self) -> str:
        return "; ".join(self.value) if isinstance(self.value, list) else str(self.value)


@dataclass
class Packet:
    props: list[Prop] = field(default_factory=list)

    def get(self, prefix: str, name: str) -> Prop | None:
        uri = SCHEMAS.get(prefix, "")
        return next((p for p in self.props if p.name == name and (p.prefix == prefix or (uri and p.uri == uri))),
                    None)

    def set(self, prefix: str, name: str, value: str | list[str], kind: str | None = None, uri: str = "") -> Prop:
        kind = kind or KNOWN_KINDS.get((prefix, name), SEQ if isinstance(value, list) else SIMPLE)
        if kind in (SEQ, BAG) and isinstance(value, str):
            value = [v.strip() for v in re.split(r"[;\n]", value) if v.strip()]
        if kind in (SIMPLE, ALT) and isinstance(value, list):
            value = "; ".join(value)
        p = self.get(prefix, name)
        if p is None:
            p = Prop(prefix, name, value, kind, uri or SCHEMAS.get(prefix, ""))
            self.props.append(p)
        else:
            p.value, p.kind, p.raw = value, kind, ""
        return p

    def remove(self, prefix: str, name: str) -> bool:
        p = self.get(prefix, name)
        if p is not None:
            self.props.remove(p)
        return p is not None

    def dublin_core(self) -> dict[str, str]:
        dc = SCHEMAS["dc"]
        return {p.name: p.text() for p in self.props if (p.uri or SCHEMAS.get(p.prefix)) == dc}


# ------------------------------------------------------------------ lettura
def _split(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        uri, _s, name = tag[1:].partition("}")
        return uri, name
    return "", tag


def _prefix_of(uri: str, declared: dict[str, str]) -> str:
    for p, u in SCHEMAS.items():
        if u == uri:
            return p
    for p, u in declared.items():
        if u == uri and p:
            return p
    return "ns"


def parse_xmp(text: str) -> Packet:
    """Proprietà del pacchetto; un XML non valido solleva ValueError con la riga dell'errore."""
    text = (text or "").strip()
    if not text:
        return Packet()
    body = re.sub(r"<\?xpacket[^>]*\?>", "", text).strip()
    declared = dict(re.findall(r'xmlns:([\w.-]+)="([^"]+)"', body))
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        line = getattr(e, "position", (0, 0))[0]
        raise ValueError(f"XMP non valido (riga {line}): {e}") from e
    packet = Packet()
    rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f"{{{RDF}}}RDF")
    if rdf is None:
        return packet
    for desc in rdf.findall(f"{{{RDF}}}Description"):      # solo quelle di primo livello
        for attr, value in desc.attrib.items():
            uri, name = _split(attr)
            if uri in (RDF, XML_NS, ""):
                continue
            packet.props.append(Prop(_prefix_of(uri, declared), name, value, SIMPLE, uri))
        for child in desc:
            uri, name = _split(child.tag)
            prefix = _prefix_of(uri, declared)
            container = next(iter(child), None)
            ctag = _split(container.tag)[1] if container is not None else ""
            if container is None:
                resource = child.attrib.get(f"{{{RDF}}}resource")
                packet.props.append(Prop(prefix, name, resource if resource is not None else (child.text or ""),
                                         SIMPLE, uri))
            elif ctag in ("Seq", "Bag") and all(_split(li.tag)[1] == "li" and not len(li) for li in container):
                packet.props.append(Prop(prefix, name, [li.text or "" for li in container],
                                         SEQ if ctag == "Seq" else BAG, uri))
            elif ctag == "Alt" and all(_split(li.tag)[1] == "li" and not len(li) for li in container):
                items = {li.attrib.get(f"{{{XML_NS}}}lang", "x-default"): li.text or "" for li in container}
                packet.props.append(Prop(prefix, name, items.get("x-default") or next(iter(items.values()), ""),
                                         ALT, uri))
            else:
                packet.props.append(Prop(prefix, name, "", RAW, uri, ET.tostring(child, encoding="unicode")))
    return packet


# ------------------------------------------------------------------ scrittura
def build_xmp(packet: Packet) -> str:
    """Pacchetto XMP completo (involucro xpacket, una sola rdf:Description)."""
    used: dict[str, str] = {}                    # prefisso -> URI
    for p in packet.props:
        uri = p.uri or SCHEMAS.get(p.prefix, "")
        if not uri or uri in used.values():
            continue
        prefix = p.prefix if p.prefix not in used else f"{p.prefix}{len(used)}"
        used[prefix] = uri
    for prefix, uri in used.items():
        ET.register_namespace(prefix, uri)
    xmpmeta = ET.Element(f"{{{X}}}xmpmeta")
    rdf = ET.SubElement(xmpmeta, f"{{{RDF}}}RDF")
    desc = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
    for p in packet.props:
        uri = p.uri or SCHEMAS.get(p.prefix, "")
        if not uri:
            continue
        if p.kind == RAW and p.raw:
            try:
                desc.append(ET.fromstring(p.raw))
                continue
            except ET.ParseError:
                continue
        el = ET.SubElement(desc, f"{{{uri}}}{p.name}")
        if p.kind in (SEQ, BAG):
            box = ET.SubElement(el, f"{{{RDF}}}{'Seq' if p.kind == SEQ else 'Bag'}")
            for v in (p.value if isinstance(p.value, list) else [p.value]):
                ET.SubElement(box, f"{{{RDF}}}li").text = v
        elif p.kind == ALT:
            box = ET.SubElement(el, f"{{{RDF}}}Alt")
            ET.SubElement(box, f"{{{RDF}}}li", {f"{{{XML_NS}}}lang": "x-default"}).text = p.text()
        else:
            el.text = p.text()
    body = ET.tostring(xmpmeta, encoding="unicode")
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + body + "\n"
            + "\n".join(" " * 100 for _i in range(20)) + '\n<?xpacket end="w"?>')


def read_packet(pdf: pymupdf.Document) -> Packet:
    try:
        return parse_xmp(pdf.get_xml_metadata())
    except ValueError:
        return Packet()


def write_packet(pdf: pymupdf.Document, packet: Packet) -> None:
    ensure_extension_schemas(packet)
    pdf.set_xml_metadata(build_xmp(packet))
    info = {}
    creator = packet.get("dc", "creator")
    title = packet.get("dc", "title")
    if creator is not None:
        info["author"] = creator.text()
    if title is not None:
        info["title"] = title.text()
    if info:                                     # /Info coerente con l'XMP
        meta = dict(pdf.metadata or {})
        meta.update(info)
        pdf.set_metadata({k: v for k, v in meta.items()
                          if k in ("title", "author", "subject", "keywords", "creator", "producer",
                                   "creationDate", "modDate", "trapped") and v is not None})


def merge(base: Packet, other: Packet, replace: bool = False) -> Packet:
    """Importa `other`: sostituisce tutto oppure unisce (le proprietà importate vincono)."""
    if replace:
        return Packet([Prop(p.prefix, p.name, p.value, p.kind, p.uri, p.raw) for p in other.props])
    out = Packet([Prop(p.prefix, p.name, p.value, p.kind, p.uri, p.raw) for p in base.props])
    for p in other.props:
        existing = next((q for q in out.props if q.key == p.key), None)
        if existing is None:
            out.props.append(Prop(p.prefix, p.name, p.value, p.kind, p.uri, p.raw))
        else:
            existing.value, existing.kind, existing.raw = p.value, p.kind, p.raw
    return out


# ------------------------------------------------------------------ PDF/A
def pdfa_part(packet: Packet) -> str | None:
    p = packet.get("pdfaid", "part")
    return p.text() if p is not None and p.text() else None


def ensure_extension_schemas(packet: Packet) -> None:
    """PDF/A: le proprietà fuori dagli schemi predefiniti (qui lo schema `foliant` delle
    proprietà personalizzate) richiedono la descrizione dello schema di estensione
    (`pdfaExtension:schemas`)."""
    if pdfa_part(packet) is None:
        return
    custom = [p for p in packet.props if (p.uri or SCHEMAS.get(p.prefix)) == SCHEMAS[CUSTOM_PREFIX]]
    if not custom:
        return
    ext = SCHEMAS["pdfaExtension"]
    sch, prop = SCHEMAS["pdfaSchema"], SCHEMAS["pdfaProperty"]
    el = ET.Element(f"{{{ext}}}schemas")
    bag = ET.SubElement(el, f"{{{RDF}}}Bag")
    li = ET.SubElement(bag, f"{{{RDF}}}li", {f"{{{RDF}}}parseType": "Resource"})
    ET.SubElement(li, f"{{{sch}}}schema").text = "Proprietà personalizzate di Foliant"
    ET.SubElement(li, f"{{{sch}}}namespaceURI").text = SCHEMAS[CUSTOM_PREFIX]
    ET.SubElement(li, f"{{{sch}}}prefix").text = CUSTOM_PREFIX
    seq = ET.SubElement(ET.SubElement(li, f"{{{sch}}}property"), f"{{{RDF}}}Seq")
    for p in custom:
        item = ET.SubElement(seq, f"{{{RDF}}}li", {f"{{{RDF}}}parseType": "Resource"})
        ET.SubElement(item, f"{{{prop}}}name").text = p.name
        ET.SubElement(item, f"{{{prop}}}valueType").text = "Text"
        ET.SubElement(item, f"{{{prop}}}category").text = "external"
        ET.SubElement(item, f"{{{prop}}}description").text = p.name
    raw = ET.tostring(el, encoding="unicode")
    existing = packet.get("pdfaExtension", "schemas")
    if existing is None:
        packet.props.append(Prop("pdfaExtension", "schemas", "", RAW, ext, raw))
    elif SCHEMAS[CUSTOM_PREFIX] not in existing.raw:
        existing.raw = _append_schema(existing.raw, li)
    else:
        existing.raw = raw if "Foliant" in existing.raw else existing.raw


def _append_schema(raw: str, li: ET.Element) -> str:
    try:
        el = ET.fromstring(raw)
    except ET.ParseError:
        return raw
    bag = el.find(f"{{{RDF}}}Bag")
    if bag is None:
        return raw
    bag.append(li)
    return ET.tostring(el, encoding="unicode")


def valid_name(name: str) -> bool:
    """Nome di proprietà XML valido (lettere, cifre, _ - .; non inizia con una cifra)."""
    return bool(re.fullmatch(r"[A-Za-z_][\w.-]*", name or ""))
