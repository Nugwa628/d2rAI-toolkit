"""What hdfx-lang knows about the CBVM (docs/hdfx-lang.md section 1): types, register classes, mop/mfn tables,
swizzle encoding, constants. Instruction layouts and text stay in `hd_cbvm` (the assembler)."""
from __future__ import annotations

import math
import re
import struct
import sys
from pathlib import Path

from hdkit.fx import cbvm as cb

NONE = cb.NONE
ORIENT = 37                     # type index of `orientation` (a quaternion; past the end of the VM name table)
TYPE_NAMES = {**{n: i for i, n in enumerate(cb.TYPES)}, "orientation": ORIENT}
VALUE_TYPES = [f"{b}{n}" for b in ("float", "int", "bool") for n in ("", "2", "3", "4")] + ["orientation"]

# register classes: bits 21-22. Order of "rates": a value's class is the highest of its inputs.
C, Q, U, S = 0, 1, 2, 3
CLASS_LETTER = "cqus"
RATE = {C: 0, Q: 1, U: 2, S: 3}

MOP = ["add", "sub", "mul", "div", "mod", "neg", "sll", "sar", "and", "or", "xor", "not",
       "lt", "le", "gt", "ge", "eq", "ne"]
MOP_UNARY = {"neg", "not"}
MFN3 = ["lerp", "clamp", "within"]
MATH = cb.MATH
BINARY = cb.BINARY
OPS = {v: k for k, v in cb.OPS.items()}       # name -> opcode
SWZ_SEL = "xyzw01"


def tname(t: int) -> str:
    if t == ORIENT:
        return "orientation"
    return cb.TYPES[t] if t < len(cb.TYPES) else str(t)


def tindex(name: str) -> int:
    return TYPE_NAMES[name]


def base(t: str) -> str:
    return "orientation" if t == "orientation" else re.sub(r"\d$", "", t)


def width(t: str) -> int:
    if t in ("orientation",):
        return 4
    if t == "class":
        return 1
    m = re.search(r"(\d)$", t)
    return int(m.group(1)) if m else 1


def vec(b: str, n: int) -> str:
    return b if n == 1 else f"{b}{n}"


# ---------------------------------------------------------------------- registers
def reg(cls: int, idx: int, t: str, flag: bool = False) -> int:
    return idx | cls << 21 | (1 << 23 if flag else 0) | tindex(t) << 24


def reg_cls(r: int) -> int:
    return (r >> 21) & 3


def reg_idx(r: int) -> int:
    return r & 0x1FFFFF


def reg_type(r: int) -> str:
    return tname((r >> 24) & 0x3F)


def reg_flag(r: int) -> bool:
    return bool((r >> 23) & 1)


# ---------------------------------------------------------------------- swizzles
_SWZ_OFF = [0, 0, 6, 42, 258]


def swz_encode(sel: str) -> list[int]:
    """The three bytes of `swz` for a selector text (`xyz1`, `x`, `111x`; 1-4 of x y z w 0 1)."""
    if not 1 <= len(sel) <= 4 or any(ch not in SWZ_SEL for ch in sel):
        raise ValueError(f"bad swizzle {sel!r}")
    d = [SWZ_SEL.index(ch) for ch in sel]
    fast = _SWZ_OFF[len(d)] + sum(v * 6 ** i for i, v in enumerate(d))
    bits = sum(v << 3 * i for i, v in enumerate(d))
    return [fast & 0xFF, bits & 0xFF, (bits >> 8) << 4 | fast >> 8]


def swz_decode(imm: list[int], n: int) -> str:
    """The selector text of a `swz` with an `n`-component destination (ValueError when the two encodings disagree)."""
    bits = (imm[2] >> 4) << 8 | imm[1]
    sel = "".join(SWZ_SEL[(bits >> 3 * i) & 7] if (bits >> 3 * i) & 7 < 6 else "?" for i in range(n))
    if "?" in sel or swz_encode(sel) != list(imm):
        raise ValueError(f"swz {imm} for {n} components does not decode")
    return sel


# ---------------------------------------------------------------------- constants
def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def const_bytes(t: str, vals) -> bytes:
    """A pool constant: the value's words (float32 or int32; bool = all bits) repeated over 32 bytes."""
    words = []
    for v in vals:
        b = base(t)
        if b == "float" or b == "orientation":
            words.append(struct.unpack("<I", struct.pack("<f", float(v)))[0])
        elif b == "bool":
            words.append(0xFFFFFFFF if v else 0)
        else:
            words.append(int(v) & 0xFFFFFFFF)
    return struct.pack("<8I", *((words * 8)[:8]))


def const_value(data: bytes, t: str) -> list:
    """The first components of a pool constant read as type `t` (float, int or bool words)."""
    n = width(t)
    w = struct.unpack("<8I", data)[:n]
    b = base(t)
    if b in ("float", "orientation"):
        return [struct.unpack("<f", struct.pack("<I", x))[0] for x in w]
    if b == "bool":
        return [x for x in w]          # raw words: 0 / 0xFFFFFFFF normally
    return [x - (1 << 32) if x >= 1 << 31 else x for x in w]


def float_text(v: float) -> str:
    """Shortest text that reads back as the same float32."""
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if v == 0:
        return "-0.0" if math.copysign(1, v) < 0 else "0.0"
    for p in range(1, 10):
        s = f"{v:.{p}g}"
        if f32(float(s)) == v:
            break
    s = repr(float(s))
    return s
