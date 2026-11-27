"""Folding of instructions whose operands are all constants (float32 maths on 32-bit words). The compiler folds with it
and `equiv` canonicalises with it, so both sides fold the same things."""
from __future__ import annotations

import math
import struct

from .isa import base, width, SWZ_SEL

M32 = 0xFFFFFFFF


def wf(w: int) -> float:
    return struct.unpack("<f", struct.pack("<I", w & M32))[0]


def fw(x: float) -> int:
    try:
        return struct.unpack("<I", struct.pack("<f", x))[0]
    except OverflowError:
        return struct.unpack("<I", struct.pack("<f", math.copysign(math.inf, x)))[0]


def wi(w: int) -> int:
    w &= M32
    return w - (1 << 32) if w >> 31 else w


def bc(t: str, n: int, ws) -> tuple:
    """Broadcast a scalar word tuple to n components."""
    ws = tuple(ws)
    return ws * n if len(ws) == 1 and n > 1 else ws


def _arith(op: str, t: str, a: tuple, b: tuple) -> tuple | None:
    if base(t) == "float":
        f = {"add": lambda x, y: x + y, "sub": lambda x, y: x - y, "mul": lambda x, y: x * y,
             "div": lambda x, y: x / y if y else (math.copysign(math.inf, x) if x else math.nan)}.get(op)
        return None if f is None else tuple(fw(f(wf(x), wf(y))) for x, y in zip(a, b))
    if base(t) == "int":
        f = {"add": lambda x, y: x + y, "sub": lambda x, y: x - y, "mul": lambda x, y: x * y}.get(op)
        return None if f is None else tuple(f(wi(x), wi(y)) & M32 for x, y in zip(a, b))
    return None


def fold(op: str, sub, dst: str, srcs: list[tuple[str, tuple]]) -> tuple | None:
    """The words of `dst` for an instruction (op name, sub-op) on constant sources [(type, words)], or None."""
    n = width(dst)
    try:
        if op in ("add", "sub", "mul", "div"):
            return _arith(op, dst, srcs[0][1], srcs[1][1])
        if op == "madd":
            if base(dst) != "float":
                return None
            return tuple(fw(wf(fw(wf(x) * wf(y))) + wf(z)) for x, y, z in zip(*(s[1] for s in srcs)))
        if op == "dup":
            return srcs[0][1][:1] * n
        if op == "vct":
            out = ()
            for _t, ws in srcs:
                out += tuple(ws)
            return out if len(out) == n else None
        if op == "swz":
            ws, out = srcs[0][1], []
            for ch in sub:
                k = SWZ_SEL.index(ch)
                if k < 4:
                    out.append(ws[k] if k < len(ws) else None)
                else:
                    out.append((fw(float(k - 4)) if base(dst) == "float" else k - 4))
            return None if None in out else tuple(out)
        if op == "cvt":
            st, ws = srcs[0]
            if base(dst) == "float" and base(st) == "int":
                return tuple(fw(float(wi(x))) for x in ws)
            if base(dst) == "int" and base(st) == "float":
                return tuple(int(wf(x)) & M32 if math.isfinite(wf(x)) else 0x80000000 for x in ws)
            if base(st) == "bool":
                return tuple((fw(1.0) if base(dst) == "float" else 1) if x else 0 for x in ws)
            return None
        if op == "mop" and sub == "neg":
            st, ws = srcs[0]
            if base(st) == "float":
                return tuple(x ^ 0x80000000 for x in ws)
            return tuple((-wi(x)) & M32 for x in ws)
        if op == "mop" and sub in ("add", "sub", "mul"):
            return _arith(sub, dst, srcs[0][1], srcs[1][1])
    except (IndexError, ValueError, ZeroDivisionError):
        return None
    return None
