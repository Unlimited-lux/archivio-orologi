"""Estrae i font .ttf da un pacchetto .deb di Ubuntu (ar + data.tar.zst) in una cartella, con la
stessa struttura di /usr/share/fonts (es. truetype/liberation/…). Serve alla CI su Windows.

Uso: python deb_fonts.py pacchetto.deb cartella"""

import io
import sys
import tarfile

import zstandard


def members(data: bytes):
    assert data[:8] == b"!<arch>\n", "non è un archivio ar"
    pos = 8
    while pos < len(data):
        header = data[pos:pos + 60]
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        yield name, data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size % 2)


deb, target = sys.argv[1], sys.argv[2]
for name, payload in members(open(deb, "rb").read()):
    if name.startswith("data.tar"):
        raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)).read() if name.endswith(".zst") else payload
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            for m in tar.getmembers():
                prefix = "./usr/share/fonts/"
                if m.isfile() and m.name.startswith(prefix) and m.name.endswith(".ttf"):
                    m.name = m.name[len(prefix):]
                    tar.extract(m, target, filter="data")
                    print(m.name)
