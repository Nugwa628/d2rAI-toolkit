"""`.hfx` source -> hd_cbvm.Script: name resolution, typing, lowering to CBVM, register allocation, constant pool,
externals and function tables (docs/hdfx-lang.md sections 2-5)."""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field

from . import fns as F
from .consteval import fold, fw
from .isa import (C, Q, U, S, RATE, MOP, MOP_UNARY, MATH, MFN3, OPS, cb, base, width, vec, reg, tindex, swz_encode,
                  VALUE_TYPES)
from .syntax import (LangError, Num, Name, Str, Member, Unary, Binary, Ternary, Call, Decl, Assign, ExprStmt, If,
                     Extern, Pragma, parse, path_of)

BACKEND = "CPU/CPU_VM"
SCOPE_CLASS = {4: S, 3: U, 2: Q, 1: Q, 0: Q}
NONE_DESC = [0, 0, 0, 0, 0]
CLASS_DESC = 0xFFFFFFFF
OBJECT_KINDS = {"ctx", "object", "curve1", "curve2", "curve3", "curve4", "shape", "vfield", "eventstream", "event"}
# built-in externals: name -> (kind, default descriptor words 2-4 or None for a stream)
BUILTIN_EXT = {"self.lifeRatio": ("float", None), "self.invLife": ("float", None), "effect.dt": ("float", [4, 2, 1]),
               "scene.dt": ("float", [4, 2, 1]), "scene.time": ("float", [4, 2, 1]), "EffectID": ("int", [4, 2, 1]),
               "ParticleContextS": ("ctx", [4, 2, 1]), "ParticleContextI": ("ctx", [3, 2, 1]),
               "RandContext": ("ctx", [4, 2, 1]), "SceneContext": ("ctx", [2, 2, 1])}
MFN1_RET = {"length": "s", "fast_length": "s", "isfinite": "b", "isinf": "b", "all": "b1", "any": "b1",
            "sincos": "f2", "fast_sincos": "f2"}
SWIZ_RE = re.compile(r"^(?:[xyzw01]{1,4}|[rgba01]{1,4})$")
CMP = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge", "==": "eq", "!=": "ne"}
BITS = {"&&": "and", "||": "or", "&": "and", "|": "or", "^": "xor", "<<": "sll", ">>": "sar", "%": "mod"}


@dataclass
class Val:
    cls: int
    type: str
    vreg: int | None = None
    const: tuple | None = None       # 32-bit words, one per component
    lit: bool = False                # an untyped literal (an int literal may become a float)

    @property
    def is_const(self) -> bool:
        return self.const is not None


@dataclass
class Ext:
    name: str | None
    kind: str                        # value type, an object kind, or "none"
    desc: list
    alias: str = ""
    explicit: bool = False
    read: bool = False
    written: bool = False


@dataclass
class IR:
    op: str                          # hd_cbvm mnemonic
    imm: list
    dst: Val | None
    srcs: list = field(default_factory=list)
    args: list = field(default_factory=list)   # call: (kind byte, Val)


class Compiler:
    def __init__(self, base_script=None, bindings=None):
        self.ir: list[IR] = []
        self.nv = 0
        self.exts: list[Ext] = []
        self.by_alias: dict[str, int] = {}
        self.by_name: dict[str, int] = {}
        self.fn_names: list[str] | None = None
        self.kind = None
        self.order = None
        self.bindings = {}
        for k, v in (bindings or {}).items():
            self.bindings[k] = v if isinstance(v, tuple) else (v, None)
        self.vals: dict = {}             # ("l", name) / ("e", index) -> Val
        self.ltypes: dict = {}
        self.frames: list[set] = []
        self.tail = None
        if base_script is not None:
            self.kind, self.order = base_script.kind, list(base_script.order)
            self.fn_names = list(base_script.fn_names) if base_script.fn_names is not None else None
            for n, d in zip(base_script.ext_names, base_script.ext_desc):
                k = "none" if n is None else (("object" if d[2] == 1 else "ctx") if d[1] == CLASS_DESC
                                              else cb.TYPES[d[1] + 1] if d[1] + 1 < len(cb.TYPES) else "orientation")
                self._add_ext(Ext(n, k, list(d), explicit=True))

    # ------------------------------------------------------------------ externals
    def _add_ext(self, e: Ext) -> int:
        self.exts.append(e)
        k = len(self.exts) - 1
        if e.name is not None:
            self.by_name.setdefault(e.name, k)
        if e.alias:
            self.by_alias[e.alias] = k
        return k

    def ext_index(self, name: str, kind: str, desc3=None, line=0) -> int:
        k = self.by_name.get(name)
        if k is not None:
            e = self.exts[k]
            if e.kind in ("object", "ctx") and kind in OBJECT_KINDS:
                e.kind = kind if kind != "ctx" or e.kind == "object" else e.kind
            elif e.kind == "?":
                e.kind = kind
                e.desc[1] = CLASS_DESC if kind in OBJECT_KINDS else tindex(kind) - 1
            return k
        if kind in OBJECT_KINDS:
            t = CLASS_DESC
        else:
            t = CLASS_DESC if kind == "?" else tindex(kind) - 1
        if desc3 is None:
            desc3 = BUILTIN_EXT.get(name, (None, None))[1]
        if desc3 is None:
            if name.startswith("__a_"):
                desc3 = [3, 2, 1]
            elif name.startswith("__sampler_"):
                desc3 = [1, 2, 1]
            elif name.startswith("__e_"):
                desc3 = [2, 2, 7]
            else:
                desc3 = [4, 0, 0]       # a stream: access filled in at the end
        return self._add_ext(Ext(name, kind, [0, t] + list(desc3)))

    def declare(self, d: Extern):
        if d.kind == "none":
            self._add_ext(Ext(None, "none", list(d.desc or NONE_DESC), d.alias, True))
            return
        name = d.ext if d.ext is not None else d.alias
        kind = d.kind
        t = CLASS_DESC if kind in OBJECT_KINDS else tindex(kind) - 1
        if d.desc is not None and len(d.desc) not in (3, 5):
            raise LangError("extern: the descriptor is [scope kind access] or all 5 words", d.line)
        if d.desc is not None and len(d.desc) == 5:
            desc = [w & 0xFFFFFFFF for w in d.desc]
        else:
            d3 = d.desc if d.desc is not None else None
            if d3 is None:
                d3 = BUILTIN_EXT.get(name, (None, None))[1]
            if d3 is None:
                d3 = ([3, 2, 1] if name.startswith("__a_") else [1, 2, 1] if kind in OBJECT_KINDS - {"ctx", "event",
                      "eventstream"} else [2, 2, 7] if kind == "event" else [2, 2, 1] if kind == "eventstream"
                      else [4, 0, 0])
            desc = [0, t] + list(d3)
        k = self.by_name.get(name)
        if k is not None and self.exts[k].explicit:       # from the base script: the source's declaration names it
            e = self.exts[k]
            e.alias, e.kind = d.alias, kind
            if d.desc is not None:
                e.desc = desc
            self.by_alias[d.alias] = k
            return
        self._add_ext(Ext(name, kind, desc, d.alias, True))

    def resolve_ext(self, p: str, line: int, kind_hint=None) -> int | None:
        """An external for a dotted name, or None."""
        if p in self.by_alias:
            return self.by_alias[p]
        if p in self.bindings:
            name, t = self.bindings[p]
            return self.ext_index(name, t or kind_hint or "?", line=line)
        if p in BUILTIN_EXT:
            return self.ext_index(p, BUILTIN_EXT[p][0], line=line)
        if p.startswith("self.") and p.count(".") == 1:
            name = p[5:]
            if name in self.by_name:
                return self.by_name[name]
            if kind_hint is None:
                raise LangError(f"{p}: unknown stream (declare it: extern float3 {p} = \"<field id>\";)", line)
            return self.ext_index(name, kind_hint, line=line)
        if p in self.by_name:
            return self.by_name[p]
        return None

    # ------------------------------------------------------------------ values
    def new(self, cls: int, t: str) -> Val:
        self.nv += 1
        return Val(cls, t, self.nv)

    def const(self, t: str, words) -> Val:
        return Val(C, t, const=tuple(int(w) & 0xFFFFFFFF for w in words))

    def emit(self, op, imm, dst, srcs=(), args=()):
        self.ir.append(IR(op, list(imm), dst, list(srcs), list(args)))
        return dst

    def cls_of(self, *vals) -> int:
        """The class of an instruction's result: the highest of its sources, at least `q` (never a constant)."""
        return max([Q] + [v.cls for v in vals], key=lambda c: RATE[c])

    def lit_to(self, v: Val, b: str) -> Val:
        """An untyped int literal used where a float (or bool) is wanted."""
        if v.is_const and v.lit and base(v.type) == "int" and b == "float":
            from .consteval import wi
            return Val(C, vec("float", width(v.type)), const=tuple(fw(float(wi(w))) for w in v.const), lit=True)
        return v

    def splat(self, v: Val, n: int, line=0) -> Val:
        if width(v.type) == n:
            return v
        if width(v.type) != 1:
            raise LangError(f"cannot use a {v.type} as a {vec(base(v.type), n)}", line)
        t = vec(base(v.type), n)
        if v.is_const:
            return Val(C, t, const=v.const * n, lit=v.lit)
        return self.emit("dup", [], self.new(v.cls, t), [v])

    def unify(self, a: Val, b: Val, line=0, allow=("float", "int")) -> tuple[Val, Val]:
        a, b = self.lit_to(a, base(b.type)), self.lit_to(b, base(a.type))
        if base(a.type) != base(b.type):
            raise LangError(f"type mismatch: {a.type} and {b.type}", line)
        if allow and base(a.type) not in allow:
            raise LangError(f"{a.type} values do not support this operator", line)
        n = max(width(a.type), width(b.type))
        return self.splat(a, n, line), self.splat(b, n, line)

    def op(self, name: str, imm, t: str, srcs: list, sub=None) -> Val:
        """An instruction on values; folded when every source is a constant."""
        if srcs and all(s.is_const for s in srcs):
            w = fold(name, sub, t, [(s.type, s.const) for s in srcs])
            if w is not None:
                return Val(C, t, const=w, lit=all(s.lit for s in srcs))
        return self.emit(name, imm, self.new(self.cls_of(*srcs), t), srcs)

    # ------------------------------------------------------------------ names
    def is_name(self, p: str) -> bool:
        return (("l", p) in self.vals or p in self.by_alias or p in self.bindings or p in BUILTIN_EXT
                or p in self.by_name or (p.startswith("self.") and p.count(".") == 1))

    def read(self, p: str, line: int) -> Val:
        if ("l", p) in self.vals:
            return self.vals[("l", p)]
        k = self.resolve_ext(p, line)
        if k is None:
            if p in ("true", "false"):
                return Val(C, "bool", const=(0xFFFFFFFF if p == "true" else 0,))
            if p in ("pi", "PI"):
                return Val(C, "float", const=(fw(3.14159265358979),))
            if p == "inf":
                return Val(C, "float", const=(0x7F800000,))
            if p == "nan":
                return Val(C, "float", const=(0x7FC00000,))
            raise LangError(f"unknown name {p!r}", line)
        return self.load(k, line)

    def load(self, k: int, line=0) -> Val:
        if ("e", k) in self.vals:
            return self.vals[("e", k)]
        e = self.exts[k]
        if e.kind in OBJECT_KINDS - {"ctx"} or e.kind == "none":
            raise LangError(f"{e.alias or e.name} is a {e.kind}, not a value", line)
        t = "class" if e.kind == "ctx" else e.kind
        if t == "?":
            raise LangError(f"{e.name}: type unknown (declare it with extern)", line)
        e.read = True
        v = self.emit("ld", [k], self.new(SCOPE_CLASS.get(e.desc[2], S), t))
        self.vals[("e", k)] = v
        return v

    def store(self, k: int, v: Val, line: int):
        e = self.exts[k]
        if e.kind == "?" or (e.kind in VALUE_TYPES and not e.explicit and not e.written and not e.read):
            e.kind = v.type
            e.desc[1] = tindex(v.type) - 1
        if e.kind not in VALUE_TYPES:
            raise LangError(f"cannot assign to {e.alias or e.name} ({e.kind})", line)
        v = self.lit_to(v, base(e.kind))
        if base(v.type) != base(e.kind):
            raise LangError(f"{e.alias or e.name} is {e.kind}, the value is {v.type}", line)
        v = self.splat(v, width(e.kind), line)
        e.written = True
        self.vals[("e", k)] = v
        if self.frames:
            self.frames[-1].add(("e", k))
        else:
            self.emit_store(k, v)

    def emit_store(self, k: int, v: Val):
        if v.is_const and not any(v.const):
            self.emit("stz", [k], None)
        else:
            self.emit("st", [k], None, [v])

    # ------------------------------------------------------------------ statements
    def run(self, items: list):
        for it in items:
            if isinstance(it, Pragma):
                self.pragma(it)
            elif isinstance(it, Extern):
                self.declare(it)
            else:
                self.stmt(it)

    def pragma(self, p: Pragma):
        if p.name == "kind":
            self.kind = None if not p.args or p.args[0] == "none" else int(p.args[0], 0)
        elif p.name == "fns":
            self.fn_names = list(p.args)
        elif p.name == "order":
            self.order = [int(a) for a in p.args]
        else:
            raise LangError(f"unknown pragma #{p.name}", p.line)

    def stmt(self, s):
        if isinstance(s, Decl):
            if ("l", s.name) in self.vals:
                raise LangError(f"{s.name} is already declared", s.line)
            v = self.expr(s.e)
            v = self.lit_to(v, base(s.type))
            if base(v.type) != base(s.type) or (width(v.type) != width(s.type) and width(v.type) != 1):
                raise LangError(f"{s.name} is {s.type}, the value is {v.type}", s.line)
            self.vals[("l", s.name)] = self.splat(v, width(s.type), s.line)
            self.ltypes[s.name] = s.type
            if self.frames:
                self.frames[-1].add(("decl", s.name))
        elif isinstance(s, Assign):
            self.assign(s)
        elif isinstance(s, ExprStmt):
            if not isinstance(s.e, Call):
                raise LangError("an expression statement must be a call", s.line)
            self.expr(s.e, stmt=True)
        elif isinstance(s, If):
            self.if_(s)
        elif isinstance(s, (Extern, Pragma)):
            raise LangError("extern and # lines go at the top level", s.line)
        else:
            raise LangError("bad statement", s.line)

    def assign(self, s: Assign):
        tgt, rhs = s.target, s.e
        if s.op != "=":
            rhs = Binary(s.op[0], tgt, rhs, line=s.line)
        p = path_of(tgt)
        if p is not None and self.is_name(p):
            v = self.expr(rhs)
            self.set_name(p, v, s.line)
            return
        if isinstance(tgt, Member) and SWIZ_RE.match(tgt.name):      # v.xz = ...
            op = path_of(tgt.obj)
            if op is None:
                raise LangError("cannot assign to this swizzle", s.line)
            old = self.read(op, s.line)
            v = self.expr(rhs)
            sel = tgt.name.translate(str.maketrans("rgba", "xyzw"))
            if any(ch in "01" for ch in sel) or len(set(sel)) != len(sel):
                raise LangError("bad swizzle target", s.line)
            n = width(old.type)
            v = self.lit_to(v, base(old.type))
            parts = []
            for i in range(n):
                ch = "xyzw"[i]
                if ch in sel:
                    j = sel.index(ch)
                    parts.append(v if width(v.type) == 1 else self.swizzle(v, "xyzw"[j], s.line))
                else:
                    parts.append(self.swizzle(old, ch, s.line))
            self.set_name(op, self.construct(old.type, parts, s.line), s.line)
            return
        raise LangError("cannot assign to this", s.line)

    def set_name(self, p: str, v: Val, line: int):
        if ("l", p) in self.vals:
            t = self.ltypes[p]
            v = self.lit_to(v, base(t))
            if base(v.type) != base(t):
                raise LangError(f"{p} is {t}, the value is {v.type}", line)
            self.vals[("l", p)] = self.splat(v, width(t), line)
            if self.frames:
                self.frames[-1].add(("l", p))
            return
        k = self.resolve_ext(p, line, kind_hint=v.type if not v.lit else ("float" if base(v.type) == "int"
                                                                           else v.type))
        if k is None:
            raise LangError(f"unknown name {p!r}", line)
        self.store(k, v, line)

    def if_(self, s: If):
        c = self.expr(s.c)
        if c.type != "bool":
            raise LangError(f"if needs a bool condition, not {c.type}", s.line)
        before = dict(self.vals)
        self.frames.append(set())
        for x in s.then:
            self.stmt(x)
        then_vals, then_set = dict(self.vals), self.frames.pop()
        self.vals = dict(before)
        self.frames.append(set())
        for x in s.other:
            self.stmt(x)
        else_vals, else_set = dict(self.vals), self.frames.pop()
        self.vals = dict(before)
        for key in sorted((then_set | else_set) - {k for k in then_set | else_set if k[0] == "decl"}, key=str):
            if key[0] == "l" and key not in before:
                continue                                   # a local declared inside the branch
            if key in before:
                old = before[key]
            elif key[0] == "e":
                old = self.load(key[1], s.line)
                before[key] = old
            a = else_vals.get(key, old)
            b = then_vals.get(key, old)
            m = self.select(a, b, c, s.line)
            self.vals[key] = m
            if self.frames:
                self.frames[-1].add(key)
            elif key[0] == "e":
                self.emit_store(key[1], m)

    # ------------------------------------------------------------------ expressions
    def expr(self, e, stmt=False) -> Val:
        if isinstance(e, Num):
            if e.is_int:
                return Val(C, "int", const=(int(e.value) & 0xFFFFFFFF,), lit=True)
            return Val(C, "float", const=(fw(float(e.value)),), lit=True)
        if isinstance(e, Name):
            return self.read(e.name, e.line)
        if isinstance(e, Member):
            p = path_of(e)
            if p is not None and self.is_name(p):
                return self.read(p, e.line)
            if not SWIZ_RE.match(e.name):
                raise LangError(f"unknown name {p or e.name!r}", e.line)
            return self.swizzle(self.expr(e.obj), e.name, e.line)
        if isinstance(e, Unary):
            return self.unary(e)
        if isinstance(e, Binary):
            return self.binary(e)
        if isinstance(e, Ternary):
            c = self.expr(e.c)
            return self.select(self.expr(e.b), self.expr(e.a), c, e.line)
        if isinstance(e, Call):
            return self.call(e, stmt)
        if isinstance(e, Str):
            raise LangError("a string is only allowed in extern and __call", e.line)
        raise LangError("bad expression", getattr(e, "line", 0))

    def swizzle(self, v: Val, sel: str, line: int) -> Val:
        sel = sel.translate(str.maketrans("rgba", "xyzw"))
        n = width(v.type)
        if any(ch in "xyzw" and "xyzw".index(ch) >= n for ch in sel) or base(v.type) not in ("float", "int", "bool"):
            raise LangError(f"swizzle .{sel} of a {v.type}", line)
        t = vec(base(v.type), len(sel))
        return self.op("swz", swz_encode(sel), t, [v], sel)

    def unary(self, e: Unary) -> Val:
        v = self.expr(e.e)
        if e.op == "-":
            if base(v.type) not in ("float", "int"):
                raise LangError(f"cannot negate a {v.type}", e.line)
            return self.mop("neg", v.type, [v])
        if e.op == "!" and base(v.type) != "bool":
            raise LangError(f"! needs a bool, not {v.type}", e.line)
        if e.op == "~" and base(v.type) not in ("int", "bool"):
            raise LangError(f"~ needs an int, not {v.type}", e.line)
        return self.mop("not", v.type, [v])

    def mop(self, sub: str, t: str, srcs: list) -> Val:
        regs = list(srcs) + ([None] if sub in MOP_UNARY else [])
        if all(s.is_const for s in srcs):
            w = fold("mop", sub, t, [(s.type, s.const) for s in srcs])
            if w is not None:
                return Val(C, t, const=w, lit=all(s.lit for s in srcs))
        return self.emit("mop", [MOP.index(sub)], self.new(self.cls_of(*srcs), t), regs)

    def binary(self, e: Binary) -> Val:
        op = e.op
        fuse_l = isinstance(e.a, Binary) and e.a.op == "*" and not getattr(e.a, "paren", False)
        fuse_r = isinstance(e.b, Binary) and e.b.op == "*" and not getattr(e.b, "paren", False)
        if op == "+" and (fuse_l or fuse_r):        # a * b + c  and  c + a * b  -> madd
            if fuse_l:
                a, b = self.expr(e.a.a), self.expr(e.a.b)
                c = self.expr(e.b)
            else:
                c = self.expr(e.a)
                a, b = self.expr(e.b.a), self.expr(e.b.b)
            prod_const = a.is_const and b.is_const
            if not prod_const and "float" in (base(a.type), base(b.type), base(c.type)):
                a, b = self.unify(a, b, e.line, ("float",))
                c = self.lit_to(c, "float")
                if base(c.type) != "float":
                    raise LangError(f"type mismatch: {a.type} and {c.type}", e.line)
                n = max(width(a.type), width(c.type))
                a, b, c = self.splat(a, n, e.line), self.splat(b, n, e.line), self.splat(c, n, e.line)
                return self.op("madd", [], a.type, [a, b, c])
            prod = self.arith("*", a, b, e.line)
            return self.arith("+", prod, c, e.line) if fuse_l else self.arith("+", c, prod, e.line)
        a, b = self.expr(e.a), self.expr(e.b)
        if op in ("+", "-", "*", "/"):
            return self.arith(op, a, b, e.line)
        if op in CMP:
            a, b = self.unify(a, b, e.line, ("float", "int", "bool") if op in ("==", "!=") else ("float", "int"))
            return self.mop(CMP[op], vec("bool", width(a.type)), [a, b])
        if op in ("&&", "||"):
            if a.type != "bool" or b.type != "bool":
                raise LangError(f"{op} needs bools", e.line)
        a, b = self.unify(a, b, e.line, ("int", "bool") if op in ("&", "|", "^", "&&", "||") else
                          ("int",) if op in ("<<", ">>") else ("float", "int"))
        return self.mop(BITS[op], a.type, [a, b])

    def arith(self, op: str, a: Val, b: Val, line: int) -> Val:
        a, b = self.unify(a, b, line)
        name = {"+": "add", "-": "sub", "*": "mul", "/": "div"}[op]
        if base(a.type) == "int" and op == "*":        # int division is `div` (as the game's scripts), int * is mop.mul
            return self.mop(name, a.type, [a, b])
        return self.op(name, [], a.type, [a, b])

    def select(self, a: Val, b: Val, c: Val, line: int) -> Val:
        """c ? b : a"""
        a, b = self.unify(a, b, line, None)
        if base(c.type) != "bool" or width(c.type) not in (1, width(a.type)):
            raise LangError(f"select needs a bool condition, not {c.type}", line)
        return self.emit("sel", [], self.new(self.cls_of(a, b, c), a.type), [a, b, c])

    def construct(self, t: str, parts: list, line: int) -> Val:
        b, n = base(t), width(t)
        if t == "orientation":
            b = "float"
        parts = [self.lit_to(p, b) for p in parts]
        if len(parts) == 1 and width(parts[0].type) == 1 and n > 1:
            if base(parts[0].type) != b:
                raise LangError(f"{t}({parts[0].type})", line)
            return self.splat(parts[0], n, line)
        if len(parts) == 1 and width(parts[0].type) == n:
            return self.convert(t, parts[0], line)
        if any(base(p.type) != b for p in parts) or sum(width(p.type) for p in parts) != n:
            raise LangError(f"{t}({', '.join(p.type for p in parts)}): the parts do not make a {t}", line)
        return self.op("vct", [], t, parts)

    def convert(self, t: str, v: Val, line: int) -> Val:
        if v.type == t:
            return v
        if width(v.type) != width(t):
            raise LangError(f"cannot convert {v.type} to {t}", line)
        if base(t) == "bool":
            z = Val(C, v.type, const=(0,) * width(v.type))
            return self.mop("ne", t, [v, z])
        if v.lit and base(v.type) == "int" and base(t) == "float":
            return self.lit_to(v, "float")
        return self.op("cvt", [], t, [v])

    # ------------------------------------------------------------------ calls
    def call(self, e: Call, stmt: bool) -> Val:
        p = path_of(e.f)
        if p is None:
            raise LangError("bad call", e.line)
        if p == "__call":
            if not e.args or not isinstance(e.args[0], Str):
                raise LangError('__call<type>("mangled_name", args...)', e.line)
            fn = F.lookup(e.args[0].value, None if e.tmpl == "void" else e.tmpl)
            if fn.ret is None and e.tmpl not in (None, "void"):
                fn.ret = e.tmpl
            args = e.args[1:]
            obj = None
            if fn.method:
                if not args or path_of(args[0]) is None:
                    raise LangError("a method's object goes first", e.line)
                obj = self.object_ext(path_of(args[0]), e.line)
                args = args[1:]
            return self.engine(fn, [self.expr(a) for a in args], obj, e.line, stmt)
        args = [self.expr(a) for a in e.args]
        if p in VALUE_TYPES:
            return self.construct(p, args, e.line)
        if p in ("asint", "asfloat", "asbool", "asorientation"):
            v = args[0]
            t = "orientation" if p == "asorientation" else vec(p[2:], width(v.type))
            return Val(v.cls, t, v.vreg, v.const)
        if p == "select":
            if len(args) != 3:
                raise LangError("select(a, b, condition)", e.line)
            return self.select(args[0], args[1], args[2], e.line)
        if p == "pow":
            a, b = self.unify(args[0], args[1], e.line, ("float",))
            return self.mfn("exp2", [self.arith("*", b, self.mfn("log2", [a], e.line), e.line)], e.line)
        short = p[5:] if p.startswith("fast.") and p[5:] in MATH else p
        name = ("fast_" + short) if p.startswith("fast.") and p[5:] in MATH else short
        if name in MATH or name in MFN3:
            return self.mfn(name, args, e.line)
        if "." in p:
            head, meth = p.rsplit(".", 1)
            if head in self.by_alias or head in self.bindings or head.startswith("__"):
                k = self.object_ext(head, e.line)
                kind = self.exts[k].kind
                if kind not in F.METHOD_PREFIX:
                    raise LangError(f"{head} is a {kind}; methods need a sampler or event extern", e.line)
                return self.engine(self.overload(F.METHOD_PREFIX[kind] + ":" + meth, args, e.line), args, k,
                                   e.line, stmt)
        return self.engine(self.overload(p, args, e.line), args, None, e.line, stmt)

    def object_ext(self, p: str, line: int) -> int:
        if p in self.by_alias:
            return self.by_alias[p]
        if p in self.bindings:
            name, t = self.bindings[p]
            return self.ext_index(name, t or "object", line=line)
        if p in self.by_name:
            return self.by_name[p]
        raise LangError(f"unknown object {p!r} (declare it: extern curve1 {p} = \"__sampler_0\";)", line)

    def overload(self, base_name: str, args: list, line: int) -> F.Fn:
        cands = F.overloads(base_name)
        if not cands:
            raise LangError(f"unknown function {base_name!r} (use __call<type>(\"mangled\", ...))", line)
        for level in (0, 1, 2):
            for f in cands:
                if len(f.params) == len(args) and all(self.fits(a, t, level) for a, t in zip(args, f.params)):
                    return f
        sig = ", ".join(a.type for a in args)
        raise LangError(f"no {base_name}({sig}); known: " + "; ".join(f"({', '.join(f.params)})" for f in cands), line)

    def fits(self, v: Val, t: str, level: int) -> bool:
        """An argument for a parameter: level 0 the same type; 1 also an int literal for a float; 2 also a scalar
        literal for a vector (broadcast). Overloads are tried level by level."""
        if v.type == t:
            return True
        if level and v.is_const and v.lit and base(t) in ("float", "int") and width(v.type) == 1 and (
                level == 2 or width(t) == 1):
            return base(v.type) == base(t) or (base(v.type) == "int" and base(t) == "float")
        return False

    def engine(self, fn: F.Fn, args: list, obj: int | None, line: int, stmt: bool) -> Val:
        if len(args) != len(fn.params):
            raise LangError(f"{fn.name} takes {len(fn.params)} arguments", line)
        if self.frames and fn.impure:
            raise LangError(f"{fn.short}() has side effects: not allowed inside if (pass the condition instead)", line)
        out = []
        for i, (a, t) in enumerate(zip(args, fn.params)):
            a = self.lit_to(a, base(t))
            if a.is_const and width(a.type) == 1 and width(t) > 1:
                a = self.splat(a, width(t))
            if a.type != t and not (t == "orientation" and a.type == "float4"):
                raise LangError(f"{fn.name}: argument {i + 1} is {t}, not {a.type}", line)
            imm = i in fn.imm
            if imm and not a.is_const:
                raise LangError(f"{fn.name}: argument {i + 1} must be a constant", line)
            out.append((F.arg_kind(t, a.cls, a.type, imm), a))
        for cx in fn.ctxs:
            v = self.load(self.ext_index(F.CTX[cx], "ctx", line=line), line)
            out.append((F.arg_kind(None, v.cls, "class", False), v))
        if self.fn_names is None:
            self.fn_names = []
        if fn.name not in self.fn_names:
            self.fn_names.append(fn.name)
        fi = self.fn_names.index(fn.name)
        if fn.ret is None:
            dst = None
            if not stmt:
                raise LangError(f"{fn.name} has no result", line)
        else:
            dst = self.new(F.result_class([a.cls for _k, a in out]), fn.ret)
        flags = F.call_flags(fn.ret, dst.cls if dst else C)
        self.emit("call", [flags, -1 if obj is None else obj, fi], dst, [], out)
        return dst if dst is not None else Val(C, "void", const=())

    def mfn(self, name: str, args: list, line: int) -> Val:
        if name in MFN3:
            if len(args) != 3:
                raise LangError(f"{name} takes 3 arguments", line)
            a, b = self.unify(args[0], args[1], line, ("float",))
            c = self.lit_to(args[2], "float")
            n = max(width(a.type), width(c.type))
            a, b, c = self.splat(a, n, line), self.splat(b, n, line), self.splat(c, n, line)
            return self.emit("mfn3", [MFN3.index(name)], self.new(self.cls_of(a, b, c), a.type), [a, b, c])
        fi = MATH.index(name)
        if name in cb.BINARY:
            if len(args) != 2:
                raise LangError(f"{name} takes 2 arguments", line)
            a, b = self.unify(args[0], args[1], line, ("float",))
            t = "float" if name == "dot" else a.type
            if name == "cross" and a.type != "float3":
                raise LangError("cross needs float3", line)
            return self.emit("mfn2", [fi], self.new(self.cls_of(a, b), t), [a, b])
        if len(args) != 1:
            raise LangError(f"{name} takes 1 argument", line)
        a = self.lit_to(args[0], "float")
        r = MFN1_RET.get(name)
        if base(a.type) != "float" and name not in ("all", "any"):
            raise LangError(f"{name} needs a float type, not {a.type}", line)
        t = {"s": "float", "b": vec("bool", width(a.type)), "b1": "bool", "f2": "float2"}.get(r, a.type)
        return self.emit("mfn1", [fi], self.new(self.cls_of(a), t), [a])

    # ------------------------------------------------------------------ back end
    def reorder_tail(self):
        """Externals the compiler added go in front of the empty entry (the table keeps its `-`, EffectID tail)."""
        none_k = next(i for i, e in enumerate(self.exts) if e.name is None)
        late = [i for i in range(none_k + 1, len(self.exts)) if not self.exts[i].explicit
                and self.exts[i].name != "EffectID"]
        if not late:
            return
        order = [i for i in range(none_k) ] + late + [i for i in range(none_k, len(self.exts)) if i not in late]
        remap = {old: new for new, old in enumerate(order)}
        self.exts = [self.exts[i] for i in order]
        for ins in self.ir:
            if ins.op in ("ld", "st", "stz"):
                ins.imm[0] = remap[ins.imm[0]]
            elif ins.op == "call" and ins.imm[1] >= 0:
                ins.imm[1] = remap[ins.imm[1]]

    def finish(self) -> cb.Script:
        # the table's tail: the empty entry and EffectID (kept where a declaration or the base put them)
        if not any(e.name is None for e in self.exts):
            self._add_ext(Ext(None, "none", list(NONE_DESC)))
        if "EffectID" not in self.by_name:
            self._add_ext(Ext("EffectID", "int", [0, 25, 4, 2, 1]))
        self.reorder_tail()
        for e in self.exts:
            if e.name is not None and not e.explicit and e.desc[3] == 0 and e.desc[4] == 0:
                e.desc[4] = (1 if e.read else 0) | (2 if e.written else 0) or 1
            if e.kind == "?":
                raise LangError(f"{e.name}: type unknown (declare it with extern)")
        consts: dict[bytes, int] = {}
        pool: list[bytes] = []
        last: dict[int, int] = {}
        for i, ins in enumerate(self.ir):
            for v in ins.srcs + [a for _k, a in ins.args]:
                if v is not None and v.vreg is not None:
                    last[v.vreg] = i
        free = {Q: [], U: [], S: []}
        top = {Q: 0, U: 0, S: 0}
        where: dict[int, int] = {}
        code = []

        def creg(v: Val) -> int:
            data = b"".join(int(w).to_bytes(4, "little") for w in (list(v.const) * 8)[:8])
            if data not in consts:
                consts[data] = len(pool)
                pool.append(data)
            return reg(C, consts[data], v.type)

        def rreg(v: Val) -> int:
            if v is None:
                return cb.NONE
            if v.is_const:
                return creg(v)
            return reg(v.cls, where[v.vreg], v.type)

        for i, ins in enumerate(self.ir):
            srcs = [rreg(v) for v in ins.srcs]
            args = [(k, rreg(v)) for k, v in ins.args]
            for v in ins.srcs + [a for _k, a in ins.args]:
                if v is not None and v.vreg is not None and last.get(v.vreg) == i and v.vreg in where:
                    heapq.heappush(free[v.cls], where.pop(v.vreg))
            dst = cb.NONE
            if ins.dst is not None and ins.dst.vreg is not None:
                c = ins.dst.cls
                idx = heapq.heappop(free[c]) if free[c] else top[c]
                top[c] = max(top[c], idx + 1)
                if ins.dst.vreg in last:
                    where[ins.dst.vreg] = idx
                else:
                    heapq.heappush(free[c], idx)
                dst = reg(c, idx, ins.dst.type)
            op = OPS[ins.op]
            if ins.op in ("ld",):
                code.append(cb.Ins(op, [dst], [ins.imm[0]]))
            elif ins.op == "st":
                code.append(cb.Ins(op, [srcs[0]], [ins.imm[0]]))
            elif ins.op == "stz":
                code.append(cb.Ins(op, [], [ins.imm[0]]))
            elif ins.op == "call":
                code.append(cb.Ins(op, [dst], list(ins.imm), args))
            else:
                code.append(cb.Ins(op, [dst] + srcs, list(ins.imm)))
        code = [cb.Ins(0x7C)] + code + [cb.Ins(0x7D)]
        head = [len(code), 0, 0, 0, 0, 0, top[Q], top[U], top[S]]
        fns = self.fn_names if self.fn_names else (self.fn_names if self.fn_names == [] else None)
        order = self.order
        if order is None:
            order = [0] + ([1] if self.kind is not None else []) + [2, 4, 5] + ([6] if fns is not None else [])
        return cb.Script(0, order, BACKEND, self.kind, head, code, pool, b"",
                         [list(e.desc) for e in self.exts], [e.name for e in self.exts], fns)


def compile_source(src: str, base=None, bindings=None) -> cb.Script:
    c = Compiler(base, bindings)
    c.run(parse(src))
    return c.finish()
