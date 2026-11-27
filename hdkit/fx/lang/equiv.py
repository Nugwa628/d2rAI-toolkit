"""Do two CBVM scripts behave the same? Each is reduced to its sequence of effects (stores, calls with side effects),
each effect's values as dataflow trees: register numbers, scheduling, constant-pool order, `!` hints and dead code do
not count; constant sub-trees fold (consteval) and a load after a store of the same external reads the stored tree."""
from __future__ import annotations

import struct

from . import fns as F
from .consteval import fold
from .isa import C, MOP, cb, reg_cls, reg_idx, reg_type, swz_decode, width


class Canon:
    def __init__(self):
        self.table: dict[tuple, int] = {}
        self.consts: dict[int, tuple] = {}

    def node(self, key: tuple) -> int:
        n = self.table.get(key)
        if n is None:
            n = self.table[key] = len(self.table)
        return n

    def const(self, t: str, data: bytes) -> int:
        """A constant by its first components (float3 constants are packed `xyzxyzxy` or `xyzxxyzx` in the
        game's files; the VM reads the first ones)."""
        ws = struct.unpack("<8I", data)[:width(t)]
        n = self.node(("c", t, ws))
        self.consts[n] = (t, ws)
        return n

    def effects(self, s: cb.Script) -> list:
        regs, stored, out, seq = {}, {}, [], 0
        names = s.ext_names

        def val(r: int):
            if r == cb.NONE:
                return None
            t = reg_type(r)
            if reg_cls(r) == C:
                return self.const(t, s.consts[reg_idx(r)])
            n, dt = regs[(reg_cls(r), reg_idx(r))]
            if t != dt:
                if n in self.consts:
                    return self.const(t, struct.pack("<8I", *((list(self.consts[n][1]) * 8)[:8])))
                return self.node(("as", t, n))
            return n

        for ins in s.code:
            nm = ins.name
            if nm in ("prlg", "eplg"):
                continue
            if nm == "st":
                out.append(("st", names[ins.imm[0]], val(ins.regs[0])))
                stored[ins.imm[0]] = val(ins.regs[0])
                continue
            if nm == "stz":
                d = s.ext_desc[ins.imm[0]]
                from .isa import tname
                t = tname(d[1] + 1)
                z = self.const(t, bytes(32))
                out.append(("st", names[ins.imm[0]], z))
                stored[ins.imm[0]] = z
                continue
            dst = ins.regs[0]
            if nm == "ld":
                k = ins.imm[0]
                n = stored.get(k)
                n = self.node(("ld", names[k], reg_type(dst), reg_cls(dst))) if n is None else n
            elif nm == "call":
                fn = s.fn_names[ins.imm[2]]
                obj = names[ins.imm[1]] if ins.imm[1] >= 0 else None
                args = tuple((k, val(r)) for k, r in ins.args)
                key = ("call", fn, ins.imm[0], obj, args, None if dst == cb.NONE else reg_type(dst))
                if F.lookup(fn).impure or dst == cb.NONE:
                    seq += 1
                    out.append(key)
                    n = self.node(("res", seq))
                else:
                    n = self.node(key)
                if dst == cb.NONE:
                    continue
            else:
                srcs = [val(r) for r in ins.regs[1:]]
                t = reg_type(dst)
                if nm == "idivmi":          # d = a / k by a magic multiply (k, magic, shift): the same as div
                    nm, srcs, ins = "div", srcs[:2], cb.Ins(0x74, ins.regs[:3], [])
                sub = None
                if nm == "swz":
                    sub = swz_decode(ins.imm, width(t))
                elif nm == "mop":
                    sub = MOP[ins.imm[0]] if ins.imm[0] < len(MOP) else ins.imm[0]
                n = None
                if srcs and all(x is None or x in self.consts for x in srcs):
                    w = fold(nm, sub, t, [self.consts[x] for x in srcs if x is not None])
                    if w is not None:
                        n = self.const(t, struct.pack("<8I", *((list(w) * 8)[:8])))
                if n is None:
                    n = self.node((nm, tuple(ins.imm), t, reg_cls(dst), tuple(srcs)))
            regs[(reg_cls(dst), reg_idx(dst))] = (n, reg_type(dst))
        return out


def diff(a: cb.Script, b: cb.Script) -> list[str]:
    """Why `b` does not behave as `a` (empty when it does)."""
    out = []
    if a.kind != b.kind:
        out.append(f"kind {a.kind} != {b.kind}")
    ea = sorted(zip(a.ext_names, map(tuple, a.ext_desc)), key=str)
    eb = sorted(zip(b.ext_names, map(tuple, b.ext_desc)), key=str)
    if ea != eb:
        out.append(f"externals differ: {sorted(set(ea) ^ set(eb), key=str)[:6]}")
    c = Canon()
    fa, fb = c.effects(a), c.effects(b)
    if fa != fb:
        for k, (x, y) in enumerate(zip(fa, fb)):
            if x != y:
                out.append(f"effect {k}: {x[:2]} != {y[:2]}")
                break
        else:
            out.append(f"{len(fa)} effects != {len(fb)}")
    return out
