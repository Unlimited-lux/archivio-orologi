"""Conversioni verso immagini: PNG, JPEG, TIFF (anche multipagina), BMP, GIF e immagine lunga."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _, page_pixmap

from .common import ConversionError, check, expand_name_template

IMAGE_FORMATS = ("png", "jpeg", "tiff", "bmp", "gif")
EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "tiff": ".tif", "bmp": ".bmp", "gif": ".gif"}
MAX_SIDE = {"jpeg": 65535, "gif": 65535, "png": 2 ** 31 - 1, "tiff": 2 ** 31 - 1, "bmp": 2 ** 31 - 1}
TIFF_COMPRESSION = {"none": None, "lzw": "tiff_lzw", "deflate": "tiff_adobe_deflate", "jpeg": "jpeg",
                    "group4": "group4"}


def render_page(page: pymupdf.Page, dpi: int, *, color: str = "color", alpha: bool = False,
                annotations: bool = True):
    """Rende la pagina (CropBox con /Rotate applicato) e restituisce un'immagine Pillow."""
    from PIL import Image

    gray = color in ("gray", "bw", "bw-dither")
    pix = page_pixmap(page.parent, page.number, hide_unsigned_signatures=True, dpi=dpi,
                      alpha=alpha and not gray, annots=annotations,
                          colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
    mode = {1: "L", 3: "RGB", 4: "RGBA"}[pix.n]
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if color == "bw":
        img = img.point(lambda v: 255 if v > 127 else 0).convert("1", dither=Image.Dither.NONE)
    elif color == "bw-dither":
        img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return img


def _prepare(img, fmt: str, options: dict):
    from PIL import Image
    if fmt in ("jpeg", "bmp") and img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    if fmt == "jpeg" and img.mode == "1":
        img = img.convert("L")
    if fmt == "gif" and img.mode not in ("P", "1", "L"):
        img = img.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
    if fmt == "bmp" and options.get("bmp_bits") == 8 and img.mode not in ("P", "1", "L"):
        img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
    return img


def _save_kwargs(fmt: str, dpi: int, options: dict, img) -> dict:
    kw: dict = {"dpi": (dpi, dpi)}
    if fmt == "jpeg":
        kw.update(quality=int(options.get("jpeg_quality", 90)), progressive=bool(options.get("progressive", False)),
                  optimize=True)
    elif fmt == "png":
        kw.update(optimize=False, compress_level=6)
    elif fmt == "tiff":
        comp = options.get("tiff_compression", "lzw" if img.mode != "1" else "group4")
        if comp == "group4" and img.mode != "1":
            comp = "lzw"
        if comp == "jpeg" and img.mode in ("1", "P", "RGBA"):
            comp = "lzw"
        if TIFF_COMPRESSION.get(comp):
            kw["compression"] = TIFF_COMPRESSION[comp]
    return kw


def _check_size(page: pymupdf.Page, dpi: int, fmt: str) -> None:
    w = page.rect.width * dpi / 72
    h = page.rect.height * dpi / 72
    limit = MAX_SIDE[fmt]
    if max(w, h) > limit:
        max_dpi = int(limit * 72 / max(page.rect.width, page.rect.height))
        raise ConversionError(_("A {dpi} dpi la pagina {p} supera il limite di {lim} pixel del formato {f}. "
                                "Risoluzione massima ammessa: {m} dpi.").format(
            dpi=dpi, p=page.number + 1, lim=limit, f=fmt.upper(), m=max_dpi))
    if w * h > 400_000_000:
        raise ConversionError(_("L'immagine della pagina {p} sarebbe troppo grande per la memoria "
                                "({mp} megapixel). Riduci la risoluzione.").format(p=page.number + 1,
                                                                                  mp=int(w * h / 1e6)))


def write_images(work: pymupdf.Document, base_path: str, job, out) -> list[str]:
    """Un file per pagina (nomi da modello) oppure un TIFF multipagina."""
    fmt = job.fmt
    o = job.options
    dpi = max(72, min(1200, int(o.get("dpi", 150))))
    color = o.get("color", "color")
    alpha = bool(o.get("transparent", False)) and fmt in ("png", "tiff")
    annots = bool(o.get("annotations", True))
    base = Path(base_path)
    ext = base.suffix if base.suffix else EXTENSIONS[fmt]
    template = o.get("name_template", "{nome}_{pagina:03}")
    n = work.page_count
    with ENGINE_LOCK:
        for i in range(n):
            _check_size(work[i], dpi, fmt)
    created: list[str] = []
    if fmt == "tiff" and o.get("multipage", False):
        tmp = out.new(base.with_suffix(ext))
        from PIL import TiffImagePlugin
        # una pagina alla volta: la memoria non cresce con il numero di pagine
        tf = TiffImagePlugin.AppendingTiffWriter(tmp, True)
        try:
            for i in range(n):
                check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
                with ENGINE_LOCK:
                    img = render_page(work[i], dpi, color=color, alpha=alpha, annotations=annots)
                img = _prepare(img, fmt, o)
                img.encoderinfo = _save_kwargs(fmt, dpi, o, img)
                img.encoderconfig = ()
                TiffImagePlugin._save(img, tf, tmp)
                tf.newFrame()
        finally:
            tf.close()
            tf.close = lambda: None    # evita una seconda chiusura alla distruzione
        if Path(tmp).stat().st_size > 4 * 1024 ** 3:
            job.warn(_("Il TIFF supera 4 GB: alcuni programmi potrebbero non aprirlo."))
        return [tmp]
    for i in range(n):
        check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
        src = job.source_index(i)
        with ENGINE_LOCK:
            img = render_page(work[i], dpi, color=color, alpha=alpha, annotations=annots)
        img = _prepare(img, fmt, o)
        try:
            name = expand_name_template(template, name=base.stem, page=src + 1, fmt=fmt)
        except (ValueError, KeyError, IndexError) as e:
            raise ConversionError(_("Modello del nome dei file non valido: «{t}».").format(t=template)) from e
        if not name or "/" in name or "\\" in name:
            raise ConversionError(_("Modello del nome dei file non valido: «{t}».").format(t=template))
        tmp = out.new(base.with_name(name + ext))
        img.save(tmp, format=fmt.upper(), **_save_kwargs(fmt, dpi, o, img))
        created.append(tmp)
    return created


def write_long_image(work: pymupdf.Document, base_path: str, job, out) -> list[str]:
    """Unisce le pagine in un'unica immagine verticale (divisa in parti se troppo alta)."""
    from PIL import Image

    o = job.options
    fmt = "jpeg" if o.get("image_format", "png") in ("jpeg", "jpg") else "png"
    dpi = max(36, min(600, int(o.get("dpi", 96))))
    gap = max(0, int(o.get("spacing", 0)))
    gap_color = tuple(o.get("spacing_color", (255, 255, 255)))
    align = o.get("align", "center")
    limit = min(int(o.get("max_height", 65535)), 65535 if fmt == "jpeg" else 10 ** 6)
    base = Path(base_path)
    ext = ".jpg" if fmt == "jpeg" else ".png"
    n = work.page_count
    with ENGINE_LOCK:
        sizes = [(int(round(work[i].rect.width * dpi / 72)), int(round(work[i].rect.height * dpi / 72)))
                 for i in range(n)]
    width = max(w for w, _h in sizes)
    total = sum(h for _w, h in sizes) + gap * (n - 1)
    if max(h for _w, h in sizes) > limit:
        raise ConversionError(_("Una pagina supera da sola l'altezza massima: riduci la risoluzione."))
    if total > limit and not o.get("split", True):
        raise ConversionError(_("L'immagine sarebbe alta {h} pixel, oltre il limite di {l}: riduci la "
                                "risoluzione o dividila in più parti.").format(h=total, l=limit))
    # raggruppa le pagine in parti che rispettano il limite
    parts: list[list[int]] = [[]]
    height = 0
    for i, (_w, h) in enumerate(sizes):
        extra = h + (gap if parts[-1] else 0)
        if parts[-1] and height + extra > limit:
            parts.append([])
            height = 0
            extra = h
        parts[-1].append(i)
        height += extra
    created = []
    for k, idx in enumerate(parts):
        ph = sum(sizes[i][1] for i in idx) + gap * (len(idx) - 1)
        canvas = Image.new("RGB", (width, ph), gap_color)
        y = 0
        for j, i in enumerate(idx):
            check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                img = render_page(work[i], dpi, annotations=bool(o.get("annotations", True)))
            x = (width - img.width) // 2 if align == "center" else 0
            canvas.paste(img, (x, y))
            y += img.height + gap
        suffix = f"_parte{k + 1}" if len(parts) > 1 else ""
        tmp = out.new(base.with_name(base.stem + suffix + ext))
        kw = {"dpi": (dpi, dpi)}
        if fmt == "jpeg":
            kw["quality"] = int(o.get("jpeg_quality", 90))
        canvas.save(tmp, format=fmt.upper(), **kw)
        created.append(tmp)
    return created
