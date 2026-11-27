"""hd_cbvm.Script -> readable `.hfx` source that compiles back to the same behaviour (docs/hdfx-lang.md section 6)."""
from __future__ import annotations

import re
import struct

from . import fns as F
from .compiler import BUILTIN_EXT, CLASS_DESC, MFN1_RET  # noqa: F401
from .consteval import wf, wi
from .isa import (C, MOP, MATH, MFN3, cb, base, width, vec, reg_cls, reg_idx, reg_type, swz_decode, tname,
                  float_text, VALUE_TYPES)
from .syntax import LangError

CTX_NAMES = set(F.CTX.values())
RESERVED = set(VALUE_TYPES) | {"if", "else", "extern", "true", "false", "pi", "PI", "inf", "nan", "select", "lerp",
                               "clamp", "pow", "asint", "asfloat", "asbool", "__call", "self", "effect", "scene",
                               "view", "fast", "void", "class", "none"} | set(MATH)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
# precedence of printed operators (higher binds tighter)
PREC = {"?:": 1, "||": 2, "&&": 3, "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7, "<": 8, "<=": 8, ">": 8, ">=": 8,
        "<<": 9, ">>": 9, "+": 10, "-": 10, "*": 11, "/": 11, "%": 11, "unary": 12, "atom": 13}
MOP_SYM = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "sll": "<<", "sar": ">>", "lt": "<", "le": "<=",
           "gt": ">", "ge": ">=", "eq": "==", "ne": "!="}


class Unsupported(LangError):
    pass


class Def:
    __slots__ = ("i", "ins", "type", "cls", "uses", "kind", "node", "temp", "ep", "ext", "name", "fn")

    def __init__(self, i, ins, t, cls):
        self.i, self.ins, self.type, self.cls = i, ins, t, cls
        self.uses: list[int] = []
        self.kind = "op"         # op | ld | ctx | call | icall (impure call)
        self.node = None
        self.temp = None
        self.ep = None
        self.ext = None
        self.fn = None


def ext_kinds(s: cb.Script) -> list[str]:
    kinds = []
    meth = {}
    for i in s.code:
        if i.op == 0x7B and i.imm[1] >= 0 and s.fn_names:
            pre = s.fn_names[i.imm[2]].split(":")[0]
            meth[i.imm[1]] = F.PREFIX_KIND.get(pre, "object")
    for k, (n, d) in enumerate(zip(s.ext_names, s.ext_desc)):
        if n is None:
            kinds.append("none")
        elif k in meth:
            kinds.append(meth[k])
        elif d[1] == CLASS_DESC:
            kinds.append("ctx" if n in CTX_NAMES or d[2] != 1 else "object")
        else:
            kinds.append(tname(d[1] + 1))
    return kinds


def make_aliases(s: cb.Script, kinds: list[str], bindings: dict | None) -> list[str]:
    rev = {}
    for a, v in (bindings or {}).items():
        rev.setdefault(v[0] if isinstance(v, tuple) else v, a)
    out, used = [], set()
    for k, n in enumerate(s.ext_names):
        if n is None:
            out.append("")
            continue
        if n in rev:
            a = rev[n]
        elif kinds[k] in VALUE_TYPES and re.fullmatch(r"[0-9A-Fa-f]+", n):
            a = f"self._{n}"
        elif n.startswith("__a_") and IDENT.match(n[4:]):
            a = n[4:]
        elif n.startswith("__sampler_") and n[10:].isdigit():
            a = f"sampler{n[10:]}"
        elif n.startswith("__e_") and IDENT.match(n[4:]):
            a = n[4:]
        elif IDENT.match(n):
            a = n
        else:
            a = f"ext_{k}"
        if a in used or a in RESERVED or a.split(".")[0] in RESERVED - {"self", "effect", "scene", "view"} or \
                F.overloads(a) or re.fullmatch(r"t\d+", a):
            a = f"ext_{k}"
        used.add(a)
        out.append(a)
    return out


def const_text(t: str, words: tuple, scalar_ok: bool) -> tuple[str, int]:
    """A constant as source text, and its precedence."""
    n, b = width(t), base(t)

    def one(w):
        if b == "float" or b == "orientation":
            x = wf(w)
            txt = float_text(x)
            if txt in ("nan", "-nan") or (x != x) or struct.pack("<f", float(txt) if txt not in ("nan", "inf", "-inf")
                                                                 else x) != struct.pack("<I", w):
                if not (txt in ("inf", "-inf") and struct.pack("<f", x) == struct.pack("<I", w)):
                    return f"asfloat({w:#x})"
            return txt
        if b == "bool":
            return "true" if w == 0xFFFFFFFF else "false" if w == 0 else f"asbool({w:#x})"
        v = wi(w)
        return str(v) if -(1 << 24) < v < (1 << 24) else f"{w:#x}"

    ws = words[:n]
    if n == 1 or (scalar_ok and len(set(ws)) == 1):
        txt = one(ws[0])
        return txt, PREC["unary"] if txt.startswith("-") else PREC["atom"]
    if len(set(ws)) == 1:
        return f"{t}({one(ws[0])})", PREC["atom"]
    return f"{t}({', '.join(one(w) for w in ws)})", PREC["atom"]


class Decompiler:
    def __init__(self, s: cb.Script, bindings=None):
        self.s = s
        self.kinds = ext_kinds(s)
        self.alias = make_aliases(s, self.kinds, bindings)
        self.defs: list[Def] = []
        self.effects: list[tuple] = []       # (i, kind, payload)
        self.stores_at: dict[int, list[int]] = {}

    # ------------------------------------------------------------------ analysis
    def operand(self, r: int, regs: dict, i: int):
        if r == cb.NONE:
            return None
        c = reg_cls(r)
        t = reg_type(r)
        if c == C:
            k = reg_idx(r)
            if k >= len(self.s.consts):
                raise Unsupported(f"constant c{k} out of range")
            return ("c", t, struct.unpack("<8I", self.s.consts[k]))
        d = regs.get((c, reg_idx(r)))
        if d is None:
            raise Unsupported(f"register {cb.reg_text(r)} read before it is written (instruction {i})")
        d.uses.append(i)
        if t != d.type:
            if width(t) != width(d.type) and d.type != "class":
                raise Unsupported(f"{cb.reg_text(r)} read as {t} after a {d.type} write")
            return ("as", t, d)
        return ("d", d)

    def analyse(self):
        s, regs = self.s, {}
        for i, ins in enumerate(s.code):
            if ins.op in (0x7C, 0x7D):
                continue
            nm = ins.name
            if nm == "idivmi":    # idivmi d, a, k, magic, shift: d = a / k (integer division by the constant k)
                d = Def(i, ins, reg_type(ins.regs[0]), reg_cls(ins.regs[0]))
                d.node = ("div", [], [self.operand(r, regs, i) for r in ins.regs[1:3]])
                regs[(d.cls, reg_idx(ins.regs[0]))] = d
                self.defs.append(d)
                continue
            if nm in ("st", "stz"):
                k = ins.imm[0]
                v = self.operand(ins.regs[0], regs, i) if nm == "st" else None
                self.effects.append((i, "st", (k, v)))
                self.stores_at.setdefault(k, []).append(i)
                continue
            if nm == "call":
                args = [(kd, self.operand(r, regs, i)) for kd, r in ins.args]
                fn = s.fn_names[ins.imm[2]] if s.fn_names and ins.imm[2] < len(s.fn_names) else None
                if fn is None:
                    raise Unsupported(f"call #{ins.imm[2]} has no name")
                f = F.lookup(fn, None if ins.regs[0] == cb.NONE else reg_type(ins.regs[0]))
                payload = (f, ins.imm, args)
                if ins.regs[0] == cb.NONE:
                    self.effects.append((i, "call", payload))
                    continue
                d = Def(i, ins, reg_type(ins.regs[0]), reg_cls(ins.regs[0]))
                d.kind, d.node, d.fn = ("icall" if f.impure else "call"), payload, f
                if f.impure:
                    self.effects.append((i, "icall", d))
                regs[(d.cls, reg_idx(ins.regs[0]))] = d
                self.defs.append(d)
                continue
            dst = ins.regs[0]
            srcs = [self.operand(r, regs, i) for r in ins.regs[1:]]
            d = Def(i, ins, reg_type(dst), reg_cls(dst))
            if nm == "ld":
                k = ins.imm[0]
                d.ext = k
                d.kind = "ctx" if self.kinds[k] == "ctx" else "ld"
            else:
                d.node = (nm, ins.imm, srcs)
            regs[(d.cls, reg_idx(dst))] = d
            self.defs.append(d)

    def plan(self):
        """Temps versus inlining (see the module docs): walk the defs from the last."""
        eff_pos = sorted(i for i, _k, _p in self.effects)
        import bisect

        def effects_between(a, b):
            return bisect.bisect_right(eff_pos, a) < bisect.bisect_left(eff_pos, b)

        by_i = {d.i: d for d in self.defs}
        for d in reversed(self.defs):
            if d.kind == "ctx":
                continue
            if not d.uses:
                if d.kind == "icall":
                    d.temp, d.ep = "stmt", d.i
                else:
                    d.temp, d.ep = "dead", d.i
                continue
            consumer_eps = []
            for j in d.uses:
                c = by_i.get(j)
                consumer_eps.append(c.ep if c is not None and c.ep is not None else j)
            if d.kind == "ld":
                if all(not any(d.i < p < ep for p in self.stores_at.get(d.ext, ())) for ep in consumer_eps):
                    d.temp, d.ep = None, max(consumer_eps)
                    continue
                d.temp, d.ep = "temp", d.i
                continue
            if len(d.uses) == 1:
                ep = consumer_eps[0]
                if d.kind != "icall" or not effects_between(d.i, ep):
                    d.temp, d.ep = None, ep
                    continue
            d.temp, d.ep = "temp", d.i

    # ------------------------------------------------------------------ printing
    def ref(self, x, want_scalar_ok=False) -> tuple[str, int]:
        if x[0] == "c":
            return const_text(x[1], x[2], want_scalar_ok)
        if x[0] == "as":
            txt, _p = self.ref(("d", x[2]))
            fn = {"int": "asint", "float": "asfloat", "bool": "asbool", "orientation": "asorientation"}.get(base(x[1]))
            if fn is None:
                raise Unsupported(f"bit cast to {x[1]}")
            return f"{fn}({txt})", PREC["atom"]
        d = x[1]
        if d.kind == "ctx":
            raise Unsupported("a context used as a value")
        if d.temp in ("temp", "stmt") and d.name:
            return d.name, PREC["atom"]
        return self.expr(d)

    def wrap(self, x, prec, scalar_ok=False, right=False) -> str:
        txt, p = self.ref(x, scalar_ok)
        return f"({txt})" if p < prec or (right and p == prec) else txt

    def expr(self, d: Def) -> tuple[str, int]:
        if d.kind == "ld":
            return self.alias[d.ext], PREC["atom"]
        if d.kind in ("call", "icall"):
            return self.call_text(d.node, d.type), PREC["atom"]
        nm, imm, srcs = d.node
        non_c = lambda k: any(s[0] != "c" for j, s in enumerate(srcs) if j != k and s is not None)  # noqa: E731
        if nm in ("add", "sub", "mul", "div"):
            sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[nm]
            p = PREC[sym]
            left = srcs[0]
            ltxt = self.wrap(left, p, non_c(0))
            if nm == "add" and left[0] == "d" and left[1].temp is None and left[1].kind == "op" and \
                    left[1].node[0] == "mul":
                ltxt = f"({ltxt})"                   # a separate mul then add, not a madd
            rtxt = self.wrap(srcs[1], p, non_c(1), right=True)
            right = srcs[1]
            if nm == "add" and right[0] == "d" and right[1].temp is None and right[1].kind == "op" and \
                    right[1].node[0] == "mul":
                rtxt = f"({rtxt})"
            return f"{ltxt} {sym} {rtxt}", p
        if nm == "madd":
            a = self.wrap(srcs[0], PREC["*"], non_c(0))
            b = self.wrap(srcs[1], PREC["*"], non_c(1), right=True)
            c = self.wrap(srcs[2], PREC["+"], non_c(2), right=True)
            if srcs[0][0] == "c" and srcs[1][0] == "c":
                raise Unsupported("madd of two constants")
            return f"{a} * {b} + {c}", PREC["+"]
        if nm == "mop":
            sub = MOP[imm[0]] if imm[0] < len(MOP) else None
            if sub is None:
                raise Unsupported(f"mop.{imm[0]}")
            b0 = base(srcs[0][1] if srcs[0][0] in ("c", "as") else srcs[0][1].type)
            if sub == "neg":
                return f"-{self.wrap(srcs[0], PREC['unary'])}", PREC["unary"]
            if sub == "not":
                return f"{'!' if b0 == 'bool' else '~'}{self.wrap(srcs[0], PREC['unary'])}", PREC["unary"]
            if sub in ("add", "sub") or (sub in ("mul", "div") and b0 != "int"):
                raise Unsupported(f"mop.{sub} on {b0}")
            if sub in ("and", "or", "xor"):
                sym = {"and": "&&", "or": "||", "xor": "^"}[sub] if b0 == "bool" and d.type == "bool" else \
                    {"and": "&", "or": "|", "xor": "^"}[sub]
            else:
                sym = MOP_SYM[sub]
            p = PREC[sym]
            return f"{self.wrap(srcs[0], p, non_c(0))} {sym} {self.wrap(srcs[1], p, non_c(1), right=True)}", p
        if nm == "sel":
            a, b, m = srcs
            return (f"{self.wrap(m, PREC['?:'] + 1)} ? {self.wrap(b, PREC['?:'] + 1, a[0] != 'c')} : "
                    f"{self.wrap(a, PREC['?:'], b[0] != 'c')}", PREC["?:"])
        if nm == "swz":
            sel = swz_decode(imm, width(d.type))
            src = srcs[0]
            txt, p = self.ref(src)
            if p < PREC["atom"] or src[0] == "c" or re.match(r"[-\d.]", txt):
                txt = f"({txt})"
            return f"{txt}.{sel}", PREC["atom"]
        if nm == "dup":
            return f"{d.type}({self.ref(srcs[0], True)[0]})", PREC["atom"]
        if nm == "vct":
            parts = [self.ref(x, x[0] == "c" and width(x[1]) == 1)[0] for x in srcs]
            if all(x[0] == "c" for x in srcs):
                raise Unsupported("vct of constants")
            return f"{d.type}({', '.join(parts)})", PREC["atom"]
        if nm == "cvt":
            return f"{d.type}({self.ref(srcs[0])[0]})", PREC["atom"]
        if nm in ("mfn1", "mfn2"):
            fn = MATH[imm[0]] if imm[0] < len(MATH) else None
            if fn is None:
                raise Unsupported(f"{nm}.{imm[0]}")
            return f"{fn}({', '.join(self.ref(x, True)[0] for x in srcs)})", PREC["atom"]
        if nm == "mfn3":
            if imm[0] >= len(MFN3):
                raise Unsupported(f"mfn3.{imm[0]}")
            return f"{MFN3[imm[0]]}({', '.join(self.ref(x, True)[0] for x in srcs)})", PREC["atom"]
        raise Unsupported(f"{nm} is not mapped")

    def call_text(self, payload, ret) -> str:
        f, imm, args = payload
        nctx = len(f.ctxs)
        vals = args[:len(args) - nctx] if nctx else args
        for (kd, x), cx in zip(args[len(args) - nctx:], f.ctxs):
            if x[0] != "d" or x[1].kind != "ctx" or self.s.ext_names[x[1].ext] != F.CTX[cx]:
                raise Unsupported(f"{f.name}: context {cx} not from {F.CTX[cx]}")
        if len(vals) != len(f.params):
            raise Unsupported(f"{f.name}: {len(vals)} arguments for {len(f.params)} parameters")
        types = [x[1] if x[0] in ("c", "as") else x[1].type for _k, x in vals]
        txt = [self.ref(x, False)[0] for _k, x in vals]
        tm = ret if ret is not None else "void"
        first = None
        if f.name in F.TABLE:
            ok = [g for g in F.overloads(f.base) if len(g.params) == len(types)
                  and all(t == p for t, p in zip(types, g.params))]
            first = ok[0] if ok else None
        if imm[1] >= 0:
            kind = self.kinds[imm[1]]
            if F.METHOD_PREFIX.get(kind) == f.method and first is f:
                return f"{self.alias[imm[1]]}.{f.short}({', '.join(txt)})"
            return f'__call<{tm}>("{f.name}", {", ".join([self.alias[imm[1]]] + txt)})'
        if f.method:
            raise Unsupported(f"{f.name} without its object")
        if first is f and f.base not in RESERVED:
            return f"{f.base}({', '.join(txt)})"
        return f'__call<{tm}>("{f.name}"{"".join(", " + t for t in txt)})'

    # ------------------------------------------------------------------ output
    def source(self) -> str:
        self.analyse()
        self.plan()
        s = self.s
        out = [f"// hdfx-lang, decompiled from CBVM object {s.obj + 1}"]
        out.append(f"#kind {'none' if s.kind is None else s.kind}")
        default = [0] + ([1] if s.kind is not None else []) + [2, 4, 5] + ([6] if s.fn_names is not None else [])
        if s.order != default:
            out.append("#order " + " ".join(map(str, s.order)))
        if s.fn_names is not None:
            out.append("#fns" + "".join(f' "{n}"' for n in s.fn_names))
        for k, (n, dsc) in enumerate(zip(s.ext_names, s.ext_desc)):
            kind = self.kinds[k]
            if n is None:
                out.append("extern none" + ("" if dsc == [0, 0, 0, 0, 0] else f" [{' '.join(map(str, dsc))}]") + ";")
                continue
            t = CLASS_DESC if kind in ("ctx", "object") or kind in F.METHOD_PREFIX else \
                (cb.TYPES.index(kind) if kind in cb.TYPES else 37) - 1
            d = dsc[2:] if dsc[0] == 0 and dsc[1] == t else [x if x < 1 << 31 else x - (1 << 32) for x in dsc]
            out.append(f'extern {kind} {self.alias[k]} = "{n}" [{" ".join(map(str, d))}];')
        out.append("")
        names = set(self.alias)
        pre = "t" if not any(re.fullmatch(r"t\d+", a) for a in names) else "tmp"
        nt = 0
        events = {i: (kind, p) for i, kind, p in self.effects}
        by_i = {x.i: x for x in self.defs}
        for d in self.defs:
            if d.temp == "temp":
                d.name = f"{pre}{nt}"
                nt += 1
        for i, ins in enumerate(s.code):
            ev = events.get(i)
            d = by_i.get(i) if ev is None or ev[0] == "icall" else None
            if ev is not None and ev[0] == "st":
                k, v = ev[1]
                if self.kinds[k] not in VALUE_TYPES:
                    raise Unsupported(f"store to {self.kinds[k]} {self.s.ext_names[k]}")
                if v is None:
                    t = self.kinds[k]
                    val = const_text(t, (0,) * width(t), True)[0]
                else:
                    val = self.ref(v, True)[0]
                out.append(f"{self.alias[k]} = {val};")
            elif ev is not None and ev[0] == "call":
                out.append(self.call_text(ev[1], None) + ";")
            elif d is not None and d.temp == "temp":
                out.append(f"{d.type} {d.name} = {self.expr(d)[0]};")
            elif d is not None and d.temp == "stmt":
                out.append(self.call_text(d.node, d.type) + ";")
        return "\n".join(out) + "\n"


def decompile_script(s: cb.Script, bindings=None) -> str:
    return Decompiler(s, bindings).source()
