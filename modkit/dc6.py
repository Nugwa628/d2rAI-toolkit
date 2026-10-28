"""DC6: the classic graphics mode's (G key) picture format, which D2R still ships and draws for UI art and icons.

A mod that adds an icon or panel to the HD sprites should add it to the matching DC6 too, or players who switch to
classic graphics see nothing (or the game asserts on a missing frame).

File: u32 version 6, flags 1, encoding 0, termination 0xEEEEEEEE, directions, frames per direction; frame pointers;
each frame: i32 flip, width, height, offset x, offset y, unknown, next block, length; RLE bytes; 3 terminator
bytes. RLE rows run bottom-up: 0x80 ends a row, a byte with the high bit skips (b & 0x7f) transparent pixels,
otherwise b palette indices follow. Colours come from a 256-colour palette (data/global/palette/<act>/pal.dat,
stored BGR; UI icons use units).
"""
from __future__ import annotations

import struct
from pathlib import Path

from .images import Image

Frame = tuple[int, int, int, int, bytes]  # width, height, offset x, offset y, RLE block


def read(path: str | Path) -> list[Frame]:
    d = Path(path).read_bytes()
    _ver, _flags, _enc, _term, dirs, fpd = struct.unpack_from("<6I", d, 0)
    ptrs = struct.unpack_from(f"<{dirs * fpd}I", d, 24)
    frames = []
    for p in ptrs:
        _flip, w, h, ox, oy, _unk, _next, length = struct.unpack_from("<8i", d, p)
        frames.append((w, h, ox, oy, d[p + 32:p + 32 + length]))
    return frames


def write(path: str | Path, frames: list[Frame]) -> None:
    head = struct.pack("<6I", 6, 1, 0, 0xEEEEEEEE, 1, len(frames))
    offset = 24 + 4 * len(frames)
    ptrs, blobs = [], []
    for w, h, ox, oy, block in frames:
        ptrs.append(offset)
        blob = struct.pack("<8i", 0, w, h, ox, oy, 0, 0, len(block)) + block + b"\xee\xee\xee"
        blobs.append(blob)
        offset += len(blob)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + struct.pack(f"<{len(ptrs)}I", *ptrs) + b"".join(blobs))


def palette(pal_dat: bytes) -> list[tuple[int, int, int]]:
    return [(pal_dat[i * 3 + 2], pal_dat[i * 3 + 1], pal_dat[i * 3]) for i in range(256)]


def decode(frame: Frame, pal: list[tuple[int, int, int]]) -> Image:
    w, h, _ox, _oy, block = frame
    out = bytearray(w * h * 4)
    x, y, i = 0, h - 1, 0
    while i < len(block) and y >= 0:
        b = block[i]
        i += 1
        if b == 0x80:
            x, y = 0, y - 1
        elif b & 0x80:
            x += b & 0x7F
        else:
            for c in block[i:i + b]:
                if 0 <= x < w:
                    r, g, bl = pal[c]
                    out[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((r, g, bl, 255))
                x += 1
            i += b
    return w, h, bytes(out)


def encode(img: Image, pal: list[tuple[int, int, int]], alpha_cut: int = 128) -> Frame:
    """RGBA -> DC6 frame with the nearest palette colours (index 0 is transparent, never used for a pixel)."""
    w, h, rgba = img
    cache: dict[tuple[int, int, int], int] = {}

    def nearest(c: tuple[int, int, int]) -> int:
        if c not in cache:
            cache[c] = min(range(1, 256), key=lambda k: (pal[k][0] - c[0]) ** 2 + (pal[k][1] - c[1]) ** 2
                           + (pal[k][2] - c[2]) ** 2)
        return cache[c]

    out = bytearray()
    for y in range(h - 1, -1, -1):
        x = 0
        while x < w:
            o = (y * w + x) * 4
            if rgba[o + 3] < alpha_cut:
                run = 0
                while x < w and rgba[(y * w + x) * 4 + 3] < alpha_cut and run < 127:
                    run += 1
                    x += 1
                if x < w:  # trailing transparency needs no skip
                    out.append(0x80 | run)
                continue
            run_px = bytearray()
            while x < w and rgba[(y * w + x) * 4 + 3] >= alpha_cut and len(run_px) < 127:
                o = (y * w + x) * 4
                run_px.append(nearest((rgba[o], rgba[o + 1], rgba[o + 2])))
                x += 1
            out.append(len(run_px))
            out += run_px
        out.append(0x80)
    return w, h, 0, 0, bytes(out)
