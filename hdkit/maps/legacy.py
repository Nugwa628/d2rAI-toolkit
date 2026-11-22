"""Small picture helpers of the map tools: DC6 frames as palette-index arrays, D2R HD .sprite sheets, act palettes.

DC6 (classic graphics): u32 version 6, flags 1, encoding 0, termination 0xEEEEEEEE, directions, frames per direction;
frame pointers; each frame: i32 flip, width, height, offset x, offset y, unknown, next block, length; the RLE block (rows
bottom-up: 0x80 ends a row, 0x80|n skips n transparent pixels, n < 0x80 is a run of n palette indices); 3 trailer bytes.

D2R .sprite, version 31 ('SpA1'): u16 31, u16 frame width, u32 strip width, u32 height, u32 0, u32 frames, u32 0, u32 0,
u32 data size, u32 4 (40 bytes), then the RGBA strip with the frames side by side (stride = strip width / frames).

numpy only. modkit.dc6 has the same DC6 reader for files; these work on bytes and arrays, as the automap tools need.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

SPRITE_HEADER = 40
UNUSED = range(225, 255)      # act-dependent palette indices (black in D2R's units palette): never picked for new pixels

Frame = tuple                 # (width, height, offset x, offset y, RLE block)


def parse_dc6(d: bytes) -> list:
    _ver, _flags, _enc, _term, dirs, fpd = struct.unpack_from("<6I", d, 0)
    ptrs = struct.unpack_from("<%dI" % (dirs * fpd), d, 24)
    frames = []
    for p in ptrs:
        _flip, w, h, ox, oy, _unk, _next, length = struct.unpack_from("<8i", d, p)
        frames.append((w, h, ox, oy, d[p + 32:p + 32 + length]))
    return frames


def build_dc6(frames: list) -> bytes:
    head = struct.pack("<6I", 6, 1, 0, 0xEEEEEEEE, 1, len(frames))
    offset = 24 + 4 * len(frames)
    ptrs, blobs = [], []
    for w, h, ox, oy, block in frames:
        ptrs.append(offset)
        blob = struct.pack("<8i", 0, w, h, ox, oy, 0, 0, len(block)) + block + b"\xee\xee\xee"
        blobs.append(blob)
        offset += len(blob)
    return head + struct.pack("<%dI" % len(ptrs), *ptrs) + b"".join(blobs)


def indices(w: int, h: int, block: bytes) -> np.ndarray:
    """Palette indices of a DC6 frame, top to bottom (0 = transparent)."""
    idx = np.zeros((h, w), np.uint8)
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
                    idx[y, x] = c
                x += 1
            i += b
    return idx


def decode(w: int, h: int, block: bytes, pal) -> np.ndarray:
    """RGBA (h, w, 4) of a DC6 frame in palette `pal` (256 x 3)."""
    idx = indices(w, h, block)
    p = np.asarray(pal, np.uint8).reshape(256, 3)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = p[idx]
    out[..., 3] = np.where(idx > 0, 255, 0)
    return out


def encode_frame(idx: np.ndarray) -> bytes:
    """DC6 block of a palette-index frame (top to bottom; 0 = transparent)."""
    out = bytearray()
    for row in np.asarray(idx)[::-1]:
        row = [int(v) for v in row]
        end = len(row)
        while end and row[end - 1] == 0:
            end -= 1
        x = 0
        while x < end:
            start = x
            if row[x] == 0:
                while x < end and row[x] == 0 and x - start < 0x7F:
                    x += 1
                out.append(0x80 | (x - start))
            else:
                while x < end and row[x] != 0 and x - start < 0x7F:
                    x += 1
                out.append(x - start)
                out += bytes(row[start:x])
        out.append(0x80)
    return bytes(out)


def sprite_bytes(frame_width: int, strip_width: int, height: int, frames: int, rgba: bytes) -> bytes:
    return b"SpA1" + struct.pack("<HHIIIIIIII", 31, frame_width, strip_width, height, 0, frames, 0, 0,
                                 len(rgba), 4) + rgba


def sprite_layout(data: bytes) -> tuple:
    """(frame width, strip width, height, frames) of a version-31 .sprite."""
    if data[:4] != b"SpA1" or len(data) < SPRITE_HEADER:
        raise ValueError("not a D2R .sprite")
    _ver, fw, sw, h = struct.unpack_from("<HHII", data, 4)
    n = struct.unpack_from("<I", data, 0x14)[0]
    return fw, sw, h, n


def sprite_frame(data: bytes, i: int) -> np.ndarray:
    """Frame i of a .sprite as RGBA (h, frame width, 4)."""
    fw, sw, h, n = sprite_layout(data)
    strip = np.frombuffer(data, np.uint8, sw * h * 4, SPRITE_HEADER).reshape(h, sw, 4)
    return strip[:, i * (sw // n):i * (sw // n) + fw].copy()


def sprite_frames(path: Path) -> list:
    data = Path(path).read_bytes()
    return [sprite_frame(data, i) for i in range(sprite_layout(data)[3])]


def act_palette(act: int, roots=None) -> np.ndarray:
    """256 x 3 uint8 RGB of act 1..5 (D2R's extract, else the game's CASC through the configured root)."""
    from hdkit.view.mapview import world
    r = roots or world.Roots()
    pal = r.palette(act)
    if getattr(r, "palette_note", None):          # a grey ramp would give wrong colours and gains without a word
        raise FileNotFoundError("act %d palette (data/global/palette/act%d/pal.dat) not in D2R's extract and no CASC "
                                "game folder configured ([game] dir in hdkit.toml)" % (act, act))
    return pal
