"""Images: PNG in/out (standard library only) and D2R's HD .sprite files.

An image here is (width, height, rgba bytes), rows top to bottom.

.sprite, version 31 (uncompressed RGBA; what D2R uses for UI art, item pictures and skill icon strips):
    'SpA1', u16 version 31, u16 frame width, u32 total width, u32 height, u32 0, u32 frame count, u32 0, u32 0,
    u32 data size, u32 4, then RGBA (one horizontal strip; frames are frame-width slices of it).
Every HD sprite has a half-size twin, <name>.lowend.sprite, used at low texture quality. Sizes that matter
(knowledge/hd-graphics.md): item pictures 98 px per inventory cell, skill icons 130 px on a 132 px stride (a strip
is at most 16384 px wide), panel backgrounds 1162 x 1507.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

Image = tuple[int, int, bytes]
SPRITE_HEAD = struct.Struct("<4sHHIIIIIIII")


# ---------------------------------------------------------------- PNG
def read_png(path: str | Path) -> Image:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos, idat, palette, trns = 8, b"", None, None
    w = h = depth = ctype = interlace = 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise ValueError(f"{path}: only 8-bit, non-interlaced PNGs are supported (save it again as RGBA)")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * channels
    raw = zlib.decompress(idat)
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        for x in range(w):
            px = line[x * channels:(x + 1) * channels]
            if ctype == 6:
                out += px
            elif ctype == 2:
                out += px + b"\xff"
            elif ctype == 0:
                out += bytes((px[0], px[0], px[0], 255))
            elif ctype == 4:
                out += bytes((px[0], px[0], px[0], px[1]))
            else:
                r, g, bb = palette[px[0]]
                alpha = trns[px[0]] if trns and px[0] < len(trns) else 255
                out += bytes((r, g, bb, alpha))
    return w, h, bytes(out)


def write_png(path: str | Path, img: Image) -> None:
    w, h, rgba = img
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- sprites
def read_sprite(path: str | Path) -> tuple[Image, int, int]:
    """Returns (strip image, frame width, frame count). Only version 31 (RGBA) is readable."""
    data = Path(path).read_bytes()
    magic, version, frame_w, width, height, _z, frames, _a, _b, size, _four = SPRITE_HEAD.unpack_from(data)
    if magic != b"SpA1" or version != 31:
        raise ValueError(f"{path}: sprite version {version} (only uncompressed version 31 is supported)")
    rgba = data[SPRITE_HEAD.size:SPRITE_HEAD.size + width * height * 4]
    return (width, height, rgba), frame_w, max(frames, 1)


def write_sprite(path: str | Path, img: Image, frame_width: int | None = None, frames: int = 1,
                 lowend: bool = True) -> None:
    """Writes path (.sprite) and, unless lowend=False, its half-size .lowend.sprite twin."""
    w, h, rgba = img
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fw = frame_width if frame_width is not None else w // max(frames, 1)
    head = SPRITE_HEAD.pack(b"SpA1", 31, fw, w, h, 0, frames, 0, 0, len(rgba), 4)
    path.write_bytes(head + rgba)
    if lowend:
        low = path.with_name(path.name.replace(".sprite", ".lowend.sprite"))
        small = scale(img, max(1, w // 2), max(1, h // 2))
        low.write_bytes(SPRITE_HEAD.pack(b"SpA1", 31, max(1, fw // 2), small[0], small[1], 0, frames, 0, 0,
                                         len(small[2]), 4) + small[2])


# ---------------------------------------------------------------- pixels
def scale(img: Image, out_w: int, out_h: int) -> Image:
    """Bilinear resize; transparent pixels don't bleed their colour into the edges."""
    w, h, src = img
    if (w, h) == (out_w, out_h):
        return img
    fx_, fy_ = w / out_w, h / out_h
    xs = []
    for x in range(out_w):
        sx = min(max((x + 0.5) * fx_ - 0.5, 0), w - 1)
        x0 = int(sx)
        xs.append((x0, min(x0 + 1, w - 1), sx - x0))
    out = bytearray(out_w * out_h * 4)
    i = 0
    for y in range(out_h):
        sy = min(max((y + 0.5) * fy_ - 0.5, 0), h - 1)
        y0 = int(sy)
        y1 = min(y0 + 1, h - 1)
        fy = sy - y0
        for x0, x1, fx in xs:
            r = g = b = a = 0.0
            for (px, py), wt in (((x0, y0), (1 - fx) * (1 - fy)), ((x1, y0), fx * (1 - fy)),
                                 ((x0, y1), (1 - fx) * fy), ((x1, y1), fx * fy)):
                o = (py * w + px) * 4
                al = src[o + 3]
                if al:
                    k = wt * al / 255
                    r += src[o] * k
                    g += src[o + 1] * k
                    b += src[o + 2] * k
                    a += k
            if a > 0:
                out[i:i + 4] = bytes((round(r / a), round(g / a), round(b / a), min(255, round(a * 255))))
            i += 4
    return out_w, out_h, bytes(out)


def crop(img: Image, x: int, y: int, cw: int, ch: int) -> Image:
    w, _h, rgba = img
    rows = [rgba[((y + r) * w + x) * 4:((y + r) * w + x + cw) * 4] for r in range(ch)]
    return cw, ch, b"".join(rows)


def paste(dst: Image, src: Image, x: int, y: int) -> Image:
    w, h, d = dst
    sw, sh, s = src
    out = bytearray(d)
    for r in range(sh):
        if 0 <= y + r < h:
            for c in range(sw):
                if 0 <= x + c < w:
                    so = (r * sw + c) * 4
                    if s[so + 3]:
                        do = ((y + r) * w + x + c) * 4
                        out[do:do + 4] = s[so:so + 4]
    return w, h, bytes(out)


def blank(w: int, h: int) -> Image:
    return w, h, bytes(w * h * 4)


def hstack(images: list[Image], stride: int, height: int) -> Image:
    """Frames side by side, each in a stride-wide cell (strip of skill icons and similar)."""
    out = blank(stride * len(images), height)
    for n, img in enumerate(images):
        out = paste(out, img, n * stride, 0)
    return out
