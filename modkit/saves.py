"""Character saves for testing a mod: back up, read and edit stats and class skills, fix checksums
(knowledge/saves.md).

    .d2s (version 0x69 = 105): signature 0xaa55aa55, version at +4, file size at +8, checksum at +0xc
      (rotate-left-1 + byte sum over the file with the checksum zeroed), class at +0x18, level at +0x1b.
      "gf" + stats bitstream (stat id, then the value in itemstatcost CSvBits bits; ends with an all-ones id),
      then "if" + one byte (hard points) per class skill in skill-ID order.
    <name>.d2rl (D2RLoader sidecar): 'D2RL', u16 version, u16 header size 0x2c, u32 body length at +0x08,
      SHA-256 of the body (+0x2c..end) at +0x0c; the body repeats the save's checksum (+0x34) and size (+0x38) and
      holds a SHA-256 of the whole .d2s at +0x3c. A stale value makes the character vanish from character select.

Stat ids are 9 bits in D2R's own format and 16 bits in D2RLoader's extended format (id_bits). Life, mana and
stamina are stored << 8. Never hand-write items: the extended item layout differs; use the debug Item Spawner.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import time
from pathlib import Path


def checksum(data: bytes) -> int:
    buf = bytearray(data)
    buf[0xC:0x10] = b"\0\0\0\0"
    s = 0
    for b in buf:
        s = (((s << 1) | (s >> 31)) & 0xFFFFFFFF)
        s = (s + b) & 0xFFFFFFFF
    return s


def finalize(path: Path, data: bytearray) -> None:
    """Writes the .d2s with size + checksum and updates the .d2rl sidecar if there is one."""
    struct.pack_into("<I", data, 8, len(data))
    struct.pack_into("<I", data, 0xC, checksum(bytes(data)))
    path.write_bytes(bytes(data))
    side = path.with_suffix(".d2rl")
    if side.exists():
        s = bytearray(side.read_bytes())
        if s[:4] == b"D2RL":
            struct.pack_into("<I", s, 0x34, struct.unpack_from("<I", data, 0xC)[0])
            struct.pack_into("<I", s, 0x38, len(data))
            s[0x3C:0x5C] = hashlib.sha256(bytes(data)).digest()
            s[0x0C:0x2C] = hashlib.sha256(bytes(s[0x2C:])).digest()
            side.write_bytes(bytes(s))


def backup(save_dir: Path, dest_root: Path | None = None, label: str = "") -> Path:
    dest_root = dest_root or save_dir.parent / "modkit-backups"
    dest = dest_root / (time.strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else ""))
    shutil.copytree(save_dir, dest)
    return dest


class BitReader:
    def __init__(self, data: bytes, pos: int):
        self.data, self.bit = data, pos * 8

    def read(self, n: int) -> int:
        v = 0
        for i in range(n):
            byte = self.data[(self.bit + i) >> 3]
            v |= ((byte >> ((self.bit + i) & 7)) & 1) << i
        self.bit += n
        return v


class BitWriter:
    def __init__(self):
        self.bits: list[int] = []

    def write(self, v: int, n: int) -> None:
        self.bits += [(v >> i) & 1 for i in range(n)]

    def bytes(self) -> bytes:
        out = bytearray((len(self.bits) + 7) // 8)
        for i, bit in enumerate(self.bits):
            out[i >> 3] |= bit << (i & 7)
        return bytes(out)


def stat_bits(itemstatcost) -> dict[int, int]:
    """stat id -> CSvBits from an itemstatcost Table (modkit.tsv)."""
    out = {}
    for r in itemstatcost.records():
        bits = itemstatcost.get(r, "CSvBits")
        if bits:
            out[itemstatcost.record_id(r)] = int(bits)
    return out


class Save:
    def __init__(self, path: str | Path, bits: dict[int, int], id_bits: int = 9):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        if struct.unpack_from("<I", self.data, 0)[0] != 0xAA55AA55:
            raise ValueError(f"{path}: not a .d2s")
        self.bits, self.id_bits = bits, id_bits
        self.gf = self.data.find(b"gf", 0x2FD)
        if self.gf < 0:
            raise ValueError("no stats section ('gf')")
        self.stats, end_bit = self._read_stats()
        self.if_pos = self.data.find(b"if", (end_bit + 7) // 8)

    def _read_stats(self) -> tuple[dict[int, int], int]:
        r = BitReader(self.data, self.gf + 2)
        stats, stop = {}, (1 << self.id_bits) - 1
        while True:
            sid = r.read(self.id_bits)
            if sid == stop:
                return stats, r.bit
            if sid not in self.bits:
                raise ValueError(f"stat {sid} has no CSvBits (wrong id_bits? extended saves use 16)")
            stats[sid] = r.read(self.bits[sid])

    def class_skills(self) -> bytes:
        n = self.data.find(b"JM", self.if_pos)  # the items section ("JM") follows
        return bytes(self.data[self.if_pos + 2:n])

    def set_class_skill(self, slot: int, points: int) -> None:
        self.data[self.if_pos + 2 + slot] = points

    def save(self) -> None:
        w = BitWriter()
        for sid in sorted(self.stats):
            w.write(sid, self.id_bits)
            w.write(self.stats[sid], self.bits[sid])
        w.write((1 << self.id_bits) - 1, self.id_bits)
        new = b"gf" + w.bytes()
        self.data[self.gf:self.if_pos] = new
        self.if_pos = self.gf + len(new)
        self.data[0x1B] = min(self.stats.get(12, self.data[0x1B]), 255)  # header level mirrors stat 12 (level)
        finalize(self.path, self.data)
