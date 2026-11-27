"""Vectorised interpreter of D2R's compiled particle scripts (PopcornFX CBVM), one numpy lane per particle.

A script (`hdkit.fx.cbvm.Script`) is straight-line code: no jumps, conditionals are `sel`. So a script runs once over
all particles of a layer at the same time: every register holds an array of shape (rows, components) where rows is the
particle count (`s` registers) or 1 (constants `c` and uniforms `u`, broadcast by numpy). Floats are float32, integers
int32, bools numpy bool; `class` registers hold context objects (`Ctx`).

    out = run(script, n, env)        # env: the runtime behind the script (externals, engine calls), see `Env`

Externals (`ld`/`st`/`stz @k`) and engine calls go through `env`. Engine calls are looked up by their mangled name in
`ENGINE` (name -> fn(ctx, *args) -> array or None, docs/hdfx.md section 2); a name not in `ENGINE` runs a stub that
returns zeros of the destination's type and is counted in `env.stats["stub_calls"]`. Functions here only need what
`Env` offers; the runtime (`hdkit.fx.sim`) gives them effect time, transforms, events and samplers.

Semantics of the opcodes: `hdkit.fx.cbvm.SEMANTICS` (decoder table of D2R.exe at 0x2a05a30, mnemonics and operator
names from its debug table at 0x2a05ae8). The meaning of engine calls is modelled from their names, their arguments
in the 41,337 corpus scripts and PopcornFX's documented script builtins, and checked against D2R.exe's kernels
(docs/hdfx-vm-re.md, with addresses); entries marked `doubtful` in `NOTES` are guesses, `game:` ones follow D2R.exe
but read game state the viewer does not have (lights, wind, visibility map, world collision).
"""
from __future__ import annotations

import collections
import math
import struct
from dataclasses import dataclass, field

import numpy as np

from . import cbvm as cb
from . import noise as nz

NONE = cb.NONE
F32, I32 = np.float32, np.int32


# ---------------------------------------------------------------------------------------------------- values
@dataclass
class Ctx:
    """A `class` value: a context object a script loads and passes to engine calls (ParticleContextS, RandContext,
    an event, a sampler ...). `name` is the external's name."""
    name: str


def rtype(r: int) -> int:
    return (r >> 24) & 0x3F


def ncomp(t: int) -> int:
    return cb.COMPONENTS[t] if t < len(cb.COMPONENTS) else 4


def zeros(t: int, rows: int = 1):
    b = cb.type_base(t)
    if b == "c":
        return Ctx("")
    k = max(1, ncomp(t))
    return np.zeros((rows, k), {"f": F32, "b": bool}.get(b, I32))


def as_base(v, base: str):
    """`v` (an array) as float/int/bool values (a numeric conversion: `cvt`, constants, externals, engine results; a
    register read under another type than its last write is a bit reinterpretation, `reinterpret`)."""
    if isinstance(v, Ctx) or base in ("c", "v"):
        return v
    v = np.asarray(v)
    if v.ndim == 1:
        v = v[:, None]
    elif v.ndim == 0:
        v = v.reshape(1, 1)
    if base == "f":
        return v if v.dtype == F32 else v.astype(F32)
    if base == "b":
        return v if v.dtype == bool else v != 0
    if v.dtype == I32:
        return v
    if v.dtype == bool:
        return v.astype(I32)
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(np.trunc(v)).astype(np.int64).clip(-2**31, 2**31 - 1).astype(I32)


def fit(v, t: int):
    """`v` shaped for a register of type `t`: base converted, a single component broadcast to all, extra cut."""
    if isinstance(v, Ctx):
        return v
    b = cb.type_base(t)
    if b == "c":
        return v
    v = as_base(v, b)
    k = max(1, ncomp(t))
    if v.shape[1] == k:
        return v
    if v.shape[1] == 1:
        return np.repeat(v, k, axis=1)
    if v.shape[1] > k:
        return v[:, :k]
    return np.concatenate([v, np.zeros((v.shape[0], k - v.shape[1]), v.dtype)], axis=1)


def const_value(c: bytes, t: int):
    b, k = cb.type_base(t), max(1, ncomp(t))
    if b == "c":
        return Ctx("const")
    if b == "f":
        a = np.frombuffer(c, "<f4")[:k].astype(F32)
    else:
        a = np.frombuffer(c, "<i4")[:k].astype(I32)
        if b == "b":
            a = a != 0
    return a.reshape(1, k)


# ---------------------------------------------------------------------------------------------------- math
def reinterpret(v, base: str):
    """`v` read as a register of another base type WITHOUT a `cvt`: D2R's executor keeps 32-bit lanes, so the bits stay
    (a float read as int is its bit pattern, a bool is 0 or all ones, any non-zero lane is true; hdfx-lang `asint` /
    `asfloat` / `asbool`). The one rule for every register read (lanes r3-decal2 `as_lanes`, r3-calibfix
    `reinterpret`, merged at the r3 integration). The corpus' masks need
    it: `asfloat(asint(effect.dt) & asint(cond))` is dt where cond holds, else 0 (vfx_vilechild_death_bloodpool's pool
    size, vfx_bloodlord1_bloodpool's fade-out; lane r3-decal2). Numeric conversion is `cvt` only (`fit` on put)."""
    if isinstance(v, Ctx) or base not in ("f", "i", "b"):
        return v
    v = np.asarray(v)
    if v.ndim == 1:
        v = v[:, None]
    elif v.ndim == 0:
        v = v.reshape(1, 1)
    want = {"f": F32, "i": I32, "b": bool}[base]
    if v.dtype == want:
        return v
    if v.dtype == bool:
        v = np.where(v, I32(-1), I32(0)).astype(I32)
    elif v.dtype not in (F32, I32):
        return as_base(v, base)
    if base == "b":
        return np.ascontiguousarray(v).view(I32) != 0
    return np.ascontiguousarray(v).view(want)


as_lanes = reinterpret          # lane r3-decal2's name


def vct_part(v, base: str):
    """One source of `vct` (D2R.exe executor 0x16d5530): the components are copied as raw 32-bit lanes, so a float
    source in an int vector (or the reverse) keeps its bits; bools convert. (Every corpus vct is float.)"""
    v = np.asarray(v)
    if base in "fi" and v.dtype in (F32, I32) and v.dtype != {"f": F32, "i": I32}[base]:
        return np.ascontiguousarray(v).view({"f": F32, "i": I32}[base])
    return as_base(v, base)


def _norm(v):
    n = np.sqrt(np.sum(v * v, axis=1, keepdims=True))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, v / np.where(n > 0, n, 1), 0).astype(F32)


def _safe(fn):
    def g(*a):
        with np.errstate(all="ignore"):
            return fn(*a)
    return g


MATH1 = {
    "sqrt": lambda a: np.sqrt(np.maximum(a, 0)), "rsqrt": lambda a: 1 / np.sqrt(a), "cbrt": np.cbrt,
    "length": lambda a: np.sqrt(np.sum(a * a, axis=1, keepdims=True)), "normalize": _norm,
    "sin": np.sin, "cos": np.cos, "sincos": lambda a: np.concatenate([np.sin(a), np.cos(a)], axis=1), "tan": np.tan,
    "asin": lambda a: np.arcsin(np.clip(a, -1, 1)), "acos": lambda a: np.arccos(np.clip(a, -1, 1)), "atan": np.arctan,
    "exp": np.exp, "exp2": np.exp2, "log": np.log, "log2": np.log2, "rcp": lambda a: 1 / a, "abs": np.abs,
    "sign": np.sign, "ceil": np.ceil, "floor": np.floor, "frac": lambda a: a - np.floor(a),
    "fracSign": lambda a: a - np.trunc(a), "saturate": lambda a: np.clip(a, 0, 1),
    "all": lambda a: np.all(a != 0, axis=1, keepdims=True), "any": lambda a: np.any(a != 0, axis=1, keepdims=True),
    "isfinite": np.isfinite, "isinf": np.isinf,
}
MATH2 = {
    "atan2": np.arctan2, "step": lambda a, b: (b >= a).astype(F32),   # HLSL step(edge, x) = x >= edge
    "discretize": lambda a, b: np.where(b != 0, np.floor(a / np.where(b != 0, b, 1) + 0.5) * b, a),
    "min": np.minimum, "max": np.maximum, "dot": lambda a, b: np.sum(a * b, axis=1, keepdims=True),
    "cross": lambda a, b: np.cross(a, b),
}
# mfn3 (D2R.exe 0x17073b0 float, 0x17086f0 int): lerp = (b - a) * c + a, clamp = min(max(a, b), c) (max first: lo > hi
# gives hi), within = 1 where b <= a <= c else 0 (1.0/0.0 for floats, 1/0 for ints; both ends inclusive).
MATH3 = {"lerp": lambda a, b, t: (b - a) * t + a, "clamp": lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi),
         "within": lambda x, lo, hi: ((x >= lo) & (x <= hi)).astype(np.result_type(x, lo, hi))}


def _mop(name: str, a, b):
    if name == "add":
        return a + b
    if name == "sub":
        return a - b
    if name == "mul":
        return a * b
    if name == "div":
        if a.dtype.kind == "f":
            return a / b
        return np.trunc(a / np.where(b == 0, 1, b)).astype(I32)
    if name == "mod":
        return np.fmod(a, np.where(b == 0, 1, b)) if a.dtype.kind != "f" else np.fmod(a, b)
    if name == "neg":
        return -a if a.dtype != bool else ~a
    if name == "sll":
        return np.left_shift(a.astype(I32), b.astype(I32) & 31)
    if name == "sar":
        return np.right_shift(a.astype(I32), b.astype(I32) & 31)
    if name in ("and", "or", "xor"):
        f = {"and": np.bitwise_and, "or": np.bitwise_or, "xor": np.bitwise_xor}[name]
        return f(a, b) if a.dtype == b.dtype else f(as_base(a, "i"), as_base(b, "i"))
    if name == "not":
        return ~a if a.dtype in (bool, I32) else (a == 0)
    return {"cmplt": np.less, "cmple": np.less_equal, "cmpgt": np.greater, "cmpge": np.greater_equal,
            "cmpeq": np.equal, "cmpne": np.not_equal}[name](a, b)


def bitwise_lanes(name: str, a, b) -> np.ndarray:
    """and / or / xor on raw 32-bit lanes (int32 result): a float operand gives its bit pattern, a bool is a lane mask
    (true = all ones), ints keep their bits. D2R's CBVM registers are untyped 32-bit lanes and the compiler's branchless
    select `x & (cond)` relies on it: rain.particles `rate & (Rain Amount > 0.35)` (lane r3-misc), druid fire cast scorch
    timers `dt & running` (r3-overlays), the death pools' growth `asfloat(asint(effect.dt) & asint(cond))` (r3-decal2),
    Frost Nova's SmokePuff fade `dt * 4 & !isRunning` (r3-calibfix). One rule since the r3 integration: `reinterpret`."""
    x, y = np.broadcast_arrays(reinterpret(a, "i"), reinterpret(b, "i"))
    f = {"and": np.bitwise_and, "or": np.bitwise_or, "xor": np.bitwise_xor}[name]
    return np.ascontiguousarray(f(x, y), dtype=I32)


# ---------------------------------------------------------------------------------------------------- environment

GAME_EYE_DISTANCE = 884.0     # the game's camera position from the unit it follows: 884.5 / 886.5 units in two captures
                              # (calib game_view.json `eye` - unit position: polish, r3-shadows), 442 units up at el 30


def game_eye(el: float = 30.0, az: float = 45.0, dist: float = GAME_EYE_DISTANCE) -> np.ndarray:
    """view.position of the game's camera (world, y up): `dist` units towards the camera (view.camera.basis' w for
    elevation el, azimuth az) from the world origin. Effects that push their particles towards the camera (the paladin
    auras: Position + 2.5 x -view.direction) then move along the view axis only, so they stay on their spot on screen.
    The old default (0, 10, 10) looked from azimuth 0, elevation 45: those auras landed 21 px left and 15 px up of the
    unit's feet (lane r3-aura2)."""
    e, z = np.radians(el), np.radians(az)
    w = np.array([np.cos(e) * np.sin(z), np.sin(e), np.cos(e) * np.cos(z)])
    return (w * dist).astype(F32)


class Env:
    """What a script runs against. The default answers for a lone script (tests, constant folding, coverage): streams
    start at zero and are kept in `self.streams`, the effect sits at the origin, time 0, everything else neutral.
    `hdkit.fx.sim` subclasses it per layer run."""

    def __init__(self, script: cb.Script | None = None, n: int = 1, seed: int = 0, t: float = 0.0, dt: float = 1 / 30,
                 attrs: dict | None = None, stats: dict | None = None):
        self.script, self.n, self.t, self.dt = script, n, t, dt
        self.rng = np.random.default_rng(seed)
        self.attrs = attrs or {}
        self.streams: dict[str, np.ndarray] = {}
        self.stats = stats if stats is not None else new_stats()
        self.killed = np.zeros(n, bool)
        self.transform = np.eye(4, dtype=F32)     # effect -> world
        self.velocity = np.zeros(3, F32)
        self.running = True
        self.camera = game_eye()                   # view.position default: the game's camera (lane r3-aura2)
        self.ground_y = 0.0
        self.collision = None                     # hdkit.fx.collide.World of scene.intersect* (None: floor slab at ground_y)
        self.handles: list = [None]               # payloads and payload elements built in this run (index = handle)
        self.emissions: list = []                 # (event descriptor, info) of generate/trigger calls in this run

    # externals
    def ext(self, k: int) -> tuple[str | None, list[int]]:
        s = self.script
        return s.ext_names[k], s.ext_desc[k]

    def load(self, k: int, t: int):
        name, d = self.ext(k)
        if name is None:
            return zeros(t)
        if d[3] == 0:                                    # particle stream
            v = self.streams.get(name)
            return zeros(t, self.n) if v is None else v
        return self.context(name, t)

    def store(self, k: int, v) -> None:
        name, d = self.ext(k)
        if name is None:
            return
        if d[3] != 0:
            self.stats["context_stores"][name] += 1
            return
        if not isinstance(v, Ctx) and v.shape[0] != self.n:
            v = np.repeat(v, self.n, axis=0)
        self.streams[name] = v

    def store_zero(self, k: int) -> None:
        name, d = self.ext(k)
        if name is None:
            return
        t = (d[1] + 1) if d[1] != NONE else 0
        self.store(k, zeros(t, self.n))

    def context(self, name: str, t: int):
        """A non-stream external: dt, attributes, context objects."""
        if cb.type_base(t) == "c" or name in ("ParticleContextS", "ParticleContextI", "RandContext", "SceneCtx"):
            return Ctx(name)
        if name in ("scene.time", "effect.time"):
            return np.full((1, 1), self.t, F32)
        if name in ("scene.dt", "effect.dt", "dt"):
            return np.asarray(self.dt, F32).reshape(-1, 1)   # one value, or one per particle (newborns)
        if name in ("EffectID",):
            return np.zeros((1, 1), I32)
        if name.startswith("__a_"):
            a = self.attrs.get(name[4:], self.attrs.get(name))
            if a is not None:
                return fit(np.asarray(a, F32).reshape(1, -1), t)
        self.stats["unknown_ext"][name] += 1
        return zeros(t)

    # hooks the runtime overrides
    def bound(self, ins: cb.Ins) -> str | None:
        """Name of the external a call is bound to (call.x.<k>): a sampler or an event."""
        k = ins.imm[1]
        return self.script.ext_names[k] if 0 <= k < len(self.script.ext_names) else None

    def kick(self, event: str | None, counts: np.ndarray, elements: dict, fracs=None) -> None:
        self.stats["kicks"][event] += int(np.sum(counts))

    def payload(self, eid: int, k: int = 1):
        return None

    def sampler(self, name: str | None):
        return None

    def per_particle(self, v):
        return v if isinstance(v, Ctx) or v.shape[0] == self.n else np.repeat(v, self.n, axis=0)


def new_stats() -> dict:
    return {"calls": collections.Counter(), "stub_calls": collections.Counter(), "unknown_ext": collections.Counter(),
            "context_stores": collections.Counter(), "kicks": collections.Counter(), "doubtful": collections.Counter(),
            "sampler_stub": collections.Counter()}


# ---------------------------------------------------------------------------------------------------- run
class CallCtx:
    """What an engine function gets as `ctx`: the environment, the call instruction and the destination type."""
    __slots__ = ("env", "ins", "dst_type", "name", "n")

    def __init__(self, env: Env, ins: cb.Ins, dst_type: int, name: str):
        self.env, self.ins, self.dst_type, self.name, self.n = env, ins, dst_type, name, env.n

    @property
    def bound(self):
        return self.env.bound(self.ins)

    def zeros(self):
        return zeros(self.dst_type, self.n) if self.dst_type else None


def run(script: cb.Script, n: int, env: Env) -> Env:
    """Run `script` over `n` particles against `env` (its streams/context). Returns `env`."""
    regs: dict[int, object] = {}
    cache: dict[tuple[int, int], np.ndarray] = {}
    consts = script.consts

    def get(r: int):
        if r == NONE:
            return None
        t, key, cls = rtype(r), r & 0x7FFFFF, (r >> 21) & 3
        if cls == 0:
            ck = (r & 0x1FFFFF, t)
            v = cache.get(ck)
            if v is None:
                idx = r & 0x1FFFFF
                v = cache[ck] = const_value(consts[idx], t) if idx < len(consts) else zeros(t)
            return v
        v = regs.get(key & 0x7FFFFF & ~(1 << 23))
        if v is None:
            return zeros(t)
        return v if isinstance(v, Ctx) else reinterpret(v, cb.type_base(t))

    def raw(r: int):
        """A register's stored value without the operand type's conversion (constants: as typed)."""
        if (r >> 21) & 3 == 0:
            return get(r)
        v = regs.get(r & 0x7FFFFF & ~(1 << 23))
        return get(r) if v is None or isinstance(v, Ctx) else v

    def put(r: int, v) -> None:
        if r == NONE or v is None:
            return
        regs[r & 0x7FFFFF & ~(1 << 23)] = fit(v, rtype(r))

    for ins in script.code:
        op = ins.op
        R = ins.regs
        if op == 0x69:                                   # ld
            put(R[0], env.load(ins.imm[0], rtype(R[0])))
        elif op == 0x6A:                                 # st
            env.store(ins.imm[0], fit(get(R[0]), rtype(R[0])))
        elif op == 0x6B:                                 # stz
            env.store_zero(ins.imm[0])
        elif op in (0x6C, 0x6F):                         # cvt, dup
            put(R[0], get(R[1]))
        elif op == 0x6D:                                 # vct
            parts = [get(r) for r in R[1:]]
            rows = max(p.shape[0] for p in parts)
            base = cb.type_base(rtype(R[0]))
            put(R[0], np.concatenate([np.broadcast_to(vct_part(p, base), (rows, p.shape[1])) for p in parts], axis=1))
        elif op == 0x6E:                                 # swz
            a = get(R[1])
            sel = cb.swz_select(ins.imm, max(1, ncomp(rtype(R[0]))))
            cols = []
            for c in sel:
                if c < a.shape[1]:
                    cols.append(a[:, c:c + 1])
                else:
                    cols.append(np.full((a.shape[0], 1), 1 if c == cb.SWZ_ONE else 0, a.dtype))
            put(R[0], np.concatenate(cols, axis=1))
        elif 0x70 <= op <= 0x74:                         # mop, add, sub, mul, div
            name = cb.MOP[ins.imm[0]] if op == 0x70 else ("add", "sub", "mul", "div")[op - 0x71]
            if name in ("and", "or", "xor") and R[2] != NONE:   # raw 32-bit lanes (bitwise_lanes)
                x, y = raw(R[1]), raw(R[2])
                if any(isinstance(v, np.ndarray) and v.dtype == F32 for v in (x, y)):
                    env.stats.setdefault("bit_select", collections.Counter())[name] += 1
                t0 = rtype(R[0])
                regs[R[0] & 0x7FFFFF & ~(1 << 23)] = fit(reinterpret(bitwise_lanes(name, x, y), cb.type_base(t0)), t0)
                continue
            a = get(R[1])
            b = get(R[2]) if R[2] != NONE else None
            if b is not None and a.dtype != b.dtype and name not in ("sll", "sar"):
                b = as_base(b, "f" if a.dtype == F32 else ("b" if a.dtype == bool else "i"))
            with np.errstate(all="ignore"):
                put(R[0], _mop(name, a, b))
        elif op == 0x75:                                 # madd
            with np.errstate(all="ignore"):
                put(R[0], get(R[1]) * get(R[2]) + get(R[3]))
        elif op == 0x76:                                 # idivmi d, a, k, magic, shift (4 uses in the corpus)
            a, k = as_base(get(R[1]), "i"), as_base(get(R[2]), "i")  # d = a / k (int, the constant k by a magic
            put(R[0], np.trunc(a / np.where(k == 0, 1, k)).astype(I32))  # multiply: (a * magic) >> (32 + shift))
        elif op in (0x77, 0x78):                         # mfn1, mfn2
            fn = cb.MATH[ins.imm[0]]
            base = fn[5:] if fn.startswith("fast_") else fn
            with np.errstate(all="ignore"):
                if op == 0x77 or base not in MATH2:
                    put(R[0], MATH1[base](as_base(get(R[1]), "f")))
                else:
                    put(R[0], MATH2[base](as_base(get(R[1]), "f"), as_base(get(R[2]), "f")))
        elif op == 0x79:                                 # mfn3
            fn = cb.MFN3[ins.imm[0]]
            with np.errstate(all="ignore"):
                put(R[0], MATH3[fn](get(R[1]), get(R[2]), get(R[3])))
        elif op == 0x7A:                                 # sel d, a, b, m: m ? b : a
            a, b, m = get(R[1]), get(R[2]), get(R[3])
            if b.dtype != a.dtype:
                b = as_base(b, "f" if a.dtype == F32 else ("b" if a.dtype == bool else "i"))
            put(R[0], np.where(as_base(m, "b"), b, a))
        elif op == 0x7B:                                 # call
            name = script.fn_names[ins.imm[2]] if script.fn_names and ins.imm[2] < len(script.fn_names) else "?"
            dt = rtype(R[0]) if R[0] != NONE else 0
            ctx = CallCtx(env, ins, dt, name)
            args = [get(r) for _k, r in ins.args]
            env.stats["calls"][name] += 1
            fn = ENGINE.get(name)
            if fn is None:
                env.stats["stub_calls"][name] += 1
                out = ctx.zeros()
            else:
                out = fn(ctx, *args)
            if R[0] != NONE:
                put(R[0], ctx.zeros() if out is None else out)
        # prlg / eplg: nothing
    return env


# ---------------------------------------------------------------------------------------------------- engine calls
ENGINE: dict[str, object] = {}
NOTES: dict[str, str] = {}   # name -> how it is modelled; "doubtful: ..." when it is a guess


def engine(*names: str, note: str = ""):
    def reg(fn):
        for n in names:
            ENGINE[n] = fn
            if note:
                NOTES[n] = note
        return fn
    return reg


def _f(v):
    return as_base(v, "f")


def _rows(ctx, *vs) -> int:
    return max([ctx.n] + [v.shape[0] for v in vs if not isinstance(v, Ctx)])


# random -----------------------------------------------------------------------------------------------
@engine("rand_float_float_RandCtx", "rand_float2_float2_RandCtx", "rand_float3_float3_RandCtx",
        "rand_float4_float4_RandCtx", note="uniform in [a, b) per component and particle")
def _rand(ctx, a, b, _r=None):
    a, b = _f(a), _f(b)
    k = max(a.shape[1], b.shape[1])
    u = ctx.env.rng.random((ctx.n, k), dtype=F32)
    return a + (b - a) * u


def _unit(rng, n):
    v = rng.normal(size=(n, 3)).astype(F32)
    return _norm(v)


@engine("vrand_RandCtx", note="random unit vector")
def _vrand(ctx, _r=None):
    return _unit(ctx.env.rng, ctx.n)


def vrand_radius(a, b, u):
    """Length of vrand(a, b) for uniforms u (D2R.exe 0xbf03c0): lo, hi = sorted(max(a, 0), max(b, 0)),
    r = lo + (hi - lo) * u ** ((1 + lo / hi) / 3) (u^(1/3) for a ball when lo = 0; 0 when hi = 0)."""
    a, b = np.maximum(a, 0), np.maximum(b, 0)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    with np.errstate(all="ignore"):
        e = np.where(hi > 0, (lo / np.where(hi > 0, hi, 1) + 1) / 3, 0)
    return (lo + (hi - lo) * np.power(u, e)).astype(F32)


@engine("vrand_float_float_RandCtx",
        note="random vector (D2R.exe 0xbf03c0): direction uniform on the sphere (y = 1 - 2u, xz from a random angle), "
             "length vrand_radius(a, b, u) = lo + (hi - lo) * u^((1 + lo/hi) / 3) with a, b clamped >= 0 and sorted")
def _vrand_ab(ctx, a, b, _r=None):
    a, b = _f(a), _f(b)
    n = _rows(ctx, a, b)
    u = ctx.env.rng.random((n, 1), dtype=F32)
    return _unit(ctx.env.rng, n) * vrand_radius(np.broadcast_to(a[:, :1], (n, 1)), np.broadcast_to(b[:, :1], (n, 1)), u)


@engine("noise_float",
        note="D2R.exe 0xbec680 -> 0xc616a0 (confirmed, bit-exact): 1D simplex noise, gradient (perm & 7) + 1 signed by bit 3, "
             "kernel (1 - d^2)^4, scale 32/81 for groups of four and 0.395 for the tail and constant inputs (hdkit.fx.noise)")
def _noise(ctx, x):
    return nz.noise_float(_f(x))


@engine("fast.noise_float",
        note="D2R.exe 0xbead00 -> 0xb3d5b0 (confirmed, bit-exact): 1D value noise over a table of 256 SFMT floats in [-1, 1) "
             "(seed 0xcafe4269) with a quintic blend (hdkit.fx.noise)")
def _fast_noise(ctx, x):
    return nz.fast_noise_float(_f(x))


@engine("noise_float3",
        note="D2R.exe 0xbec8c0 -> 0xc623e0 (confirmed, bit-exact): 3D simplex noise, classic permutation, Perlin's 12 "
             "gradients picked by hash & 15, kernel (0.6 - d^2)^4, scale 32 (hdkit.fx.noise)")
def _noise3(ctx, x):
    return nz.noise_float3(_f(x))


# colour -----------------------------------------------------------------------------------------------
@engine("hsv2rgb_float4", note="HSV(A) -> RGB(A), hue in [0, 1]")
def _hsv2rgb(ctx, c):
    c = _f(c)
    h, s, v = c[:, 0:1], c[:, 1:2], c[:, 2:3]
    k = (np.array([5, 3, 1], F32) + h * 6) % 6
    rgb = v - v * s * np.clip(np.minimum(k, 4 - k), 0, 1)
    return np.concatenate([rgb, c[:, 3:4] if c.shape[1] > 3 else np.ones_like(h)], axis=1)


@engine("rgb2hsv_float4", note="RGB(A) -> HSV(A), hue in [0, 1]")
def _rgb2hsv(ctx, c):
    c = _f(c)
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    mx, mn = np.max(c[:, :3], axis=1), np.min(c[:, :3], axis=1)
    d = mx - mn
    with np.errstate(all="ignore"):
        h = np.where(d == 0, 0, np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)))
        s = np.where(mx == 0, 0, d / mx)
    a = c[:, 3] if c.shape[1] > 3 else np.ones_like(r)
    return np.stack([h / 6, s, mx, a], axis=1).astype(F32)


# orientations (quaternions x, y, z, w) -----------------------------------------------------------------
def qmul(a, b):
    ax, ay, az, aw = (a[:, i] for i in range(4))
    bx, by, bz, bw = (b[:, i] for i in range(4))
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], axis=1).astype(F32)


def qrot(q, v):
    u, w = q[:, :3], q[:, 3:4]
    t = 2 * np.cross(u, v)
    return (v + w * t + np.cross(u, t)).astype(F32)


def q_from_to(a, b):
    """Shortest rotation taking unit vector a to unit vector b."""
    a, b = _norm(a), _norm(b)
    c = np.cross(a, b)
    w = 1 + np.sum(a * b, axis=1, keepdims=True)
    q = np.concatenate([c, w], axis=1)
    flip = (w[:, 0] < 1e-6)
    if np.any(flip):
        q[flip] = np.array([1, 0, 0, 0], F32)
    return _norm(q)


@engine("rotate_float3_orientation", note="v rotated by quaternion q")
def _rotq(ctx, v, q):
    v, q = _f(v), _f(q)
    n = _rows(ctx, v, q)
    return qrot(np.broadcast_to(q, (n, 4)), np.broadcast_to(v, (n, 3)))


@engine("orientation_mult_orientation_orientation", note="quaternion product a * b")
def _qmul(ctx, a, b):
    a, b = _f(a), _f(b)
    n = _rows(ctx, a, b)
    return qmul(np.broadcast_to(a, (n, 4)), np.broadcast_to(b, (n, 4)))


def _axis(vec):
    def f(ctx, q):
        q = _f(q)
        return qrot(q, np.broadcast_to(np.array([vec], F32), (q.shape[0], 3)))
    return f


ENGINE["orientation_axisUp_orientation"] = _axis([0, 1, 0])
ENGINE["orientation_axisSide_orientation"] = _axis([1, 0, 0])
ENGINE["orientation_axisForward_orientation"] = _axis([0, 0, 1])
for _n in ("orientation_axisUp_orientation", "orientation_axisSide_orientation", "orientation_axisForward_orientation"):
    NOTES[_n] = ("q rotates the frame's axis (D2R.exe 0xbecfd0/0xbed020/0xbecf80 -> 0xbe9110/0xbe9510/0xbe98f0, matrix "
                 "columns of q): side +x, up +y, forward +z; D2R sets coordinate frame 0 at start (0xeba840(0) -> "
                 "0xa95a90), table 0x27da6c0[0] = (1, 2, 3)")


@engine("radians.rotate_float3_float3_float", note="v rotated about axis by angle (radians, Rodrigues)")
def _rot_axis(ctx, v, axis, ang):
    v, k, a = _f(v), _norm(_f(axis)), _f(ang)
    c, s = np.cos(a), np.sin(a)
    return (v * c + np.cross(k, v) * s + k * np.sum(k * v, axis=1, keepdims=True) * (1 - c)).astype(F32)


def q_arc(ref, d, alt):
    """Shortest rotation taking axis `ref` to direction d as D2R.exe 0xbf3330 builds it: d = 0 -> identity; d
    (anti)parallel to ref (|ref x d|^2 <= 1e-5) -> rotation about `alt` by the angle; q = (axis * sqrt((1 - c) / 2),
    sqrt((1 + c) / 2)), c = dot clamped to [-1, 1]."""
    d = _f(d)
    n = d.shape[0]
    ref = np.broadcast_to(np.asarray(ref, F32).reshape(1, 3), (n, 3))
    l2 = np.sum(d * d, axis=1, keepdims=True)
    with np.errstate(all="ignore"):
        dn = np.where(l2 > 1e-8, d / np.sqrt(np.where(l2 > 0, l2, 1)), ref)
    ax = np.cross(ref, dn)
    a2 = np.sum(ax * ax, axis=1, keepdims=True)
    with np.errstate(all="ignore"):
        ax = np.where(a2 > 1e-5, ax / np.sqrt(np.where(a2 > 0, a2, 1)), np.asarray(alt, F32).reshape(1, 3))
    c = np.clip(np.sum(ref * dn, axis=1, keepdims=True), -1, 1)
    return np.concatenate([ax * np.sqrt(0.5 - 0.5 * c), np.sqrt(0.5 + 0.5 * c)], axis=1).astype(F32)


@engine("scene.orientation_u_norm_float3",
        note="rotation taking up (+y) to the direction (D2R.exe 0xbf4dd0 -> 0xbf3330(mode 0): EAxis Up, fallback axis "
             "Right +x); vm.q_arc")
def _orient_u(ctx, d):
    return q_arc([0, 1, 0], d, [1, 0, 0])


@engine("scene.orientation_f_norm_float3",
        note="rotation taking forward (EAxis Forward = -z in D2R's frame 0) to the direction (D2R.exe 0xbf3ab0 -> "
             "0xbf3330(mode 1), fallback axis Down -y); vm.q_arc")
def _orient_f(ctx, d):
    return q_arc([0, 0, -1], d, [0, -1, 0])


@engine("scene.orientation_fu_norm_float3_float3",
        note="basis rotation (D2R.exe 0xbf3c90): F = normalize(f), S = normalize(cross(u, F)) (else cross(+y, F), else "
             "+x), U = normalize(cross(F, S)); rows x, y, z = S, U, F, so +z goes to f and +y towards u")
def _orient2(ctx, f, u):
    f, u = _norm(_f(f)), _f(u)
    n = _rows(ctx, f, u)
    f, u = np.broadcast_to(f, (n, 3)), np.broadcast_to(u, (n, 3))
    q1 = q_from_to(np.broadcast_to(np.array([[0, 0, 1]], F32), (n, 3)), f)
    up1 = qrot(q1, np.broadcast_to(np.array([[0, 1, 0]], F32), (n, 3)))
    u_proj = _norm(u - f * np.sum(u * f, axis=1, keepdims=True))
    q2 = q_from_to(up1, u_proj)
    return qmul(q2, q1)


# effect, transforms --------------------------------------------------------------------------------------
def _env_q(env):
    m = env.transform[:3, :3]
    s = np.linalg.norm(m, axis=0)
    r = m / np.where(s > 0, s, 1)
    return mat_quat(r).reshape(1, 4).astype(F32)


def mat_quat(r) -> np.ndarray:
    """Rotation matrix -> unit quaternion (x, y, z, w), w >= 0, from the largest of w, x, y, z (Shepperd), so turns
    near 180 degrees keep their axis. Lane r3-calibfix: the old trace-only form gave the identity at w ~ 0, so an effect
    turned 180 degrees (a missile flying -z, `--toward 0,-N`) read effect.orientation() unturned and Fire Bolt's
    MeshFlameHead (effect.orientation() x 90 degrees about x) faced backwards: tip up instead of down on screen."""
    r = np.asarray(r, float)
    t = r[0, 0] + r[1, 1] + r[2, 2]
    if t > 0:
        k = math.sqrt(1.0 + t) * 2
        q = [(r[2, 1] - r[1, 2]) / k, (r[0, 2] - r[2, 0]) / k, (r[1, 0] - r[0, 1]) / k, k / 4]
    elif r[0, 0] >= r[1, 1] and r[0, 0] >= r[2, 2]:
        k = math.sqrt(max(1e-30, 1.0 + r[0, 0] - r[1, 1] - r[2, 2])) * 2
        q = [k / 4, (r[0, 1] + r[1, 0]) / k, (r[0, 2] + r[2, 0]) / k, (r[2, 1] - r[1, 2]) / k]
    elif r[1, 1] >= r[2, 2]:
        k = math.sqrt(max(1e-30, 1.0 + r[1, 1] - r[0, 0] - r[2, 2])) * 2
        q = [(r[0, 1] + r[1, 0]) / k, k / 4, (r[1, 2] + r[2, 1]) / k, (r[0, 2] - r[2, 0]) / k]
    else:
        k = math.sqrt(max(1e-30, 1.0 + r[2, 2] - r[0, 0] - r[1, 1])) * 2
        q = [(r[0, 2] + r[2, 0]) / k, (r[1, 2] + r[2, 1]) / k, k / 4, (r[1, 0] - r[0, 1]) / k]
    q = np.array(q, float)
    q /= max(float(np.linalg.norm(q)), 1e-30)
    return q if q[3] >= 0 else -q


@engine("effect.age_pCtxI", note="seconds since the effect started")
def _age(ctx, _c=None):
    return np.full((1, 1), ctx.env.t, F32)


@engine("effect.isRunning_pCtxI", note="True until Sim.stop()")
def _running(ctx, _c=None):
    return np.full((1, 1), ctx.env.running, bool)


@engine("effect.isRenderingEnabled_pCtxI", note="always True")
def _rendering(ctx, _c=None):
    return np.ones((1, 1), bool)


@engine("effect.isTeleporting_pCtxI", "_pksi_vvHasTeleported_SI", note="never")
def _teleport(ctx, *_a):
    return np.zeros((1, 1), bool)


@engine("effect.position_pCtxI", note="SimContext transform translation")
def _epos(ctx, _c=None):
    return ctx.env.transform[:3, 3].reshape(1, 3).astype(F32)


@engine("effect.velocity_pCtxI", note="SimContext velocity")
def _evel(ctx, _c=None):
    return np.asarray(ctx.env.velocity, F32).reshape(1, 3)


@engine("effect.orientation_pCtxI", note="SimContext transform rotation")
def _eori(ctx, _c=None):
    return _env_q(ctx.env)


def _eaxis(col):
    def f(ctx, _c=None):
        return _norm(ctx.env.transform[:3, col].reshape(1, 3).astype(F32))
    return f


ENGINE["effect.axisSide_pCtxI"] = _eaxis(0)
ENGINE["effect.axisUp_pCtxI"] = _eaxis(1)
ENGINE["effect.axisForward_pCtxI"] = lambda ctx, _c=None: -_eaxis(2)(ctx)
for _n, _s in (("effect.axisSide_pCtxI", "Right +column 0"), ("effect.axisUp_pCtxI", "Up +column 1"),
               ("effect.axisForward_pCtxI", "Forward = -column 2")):
    NOTES[_n] = (f"effect rotation times the frame axis (D2R.exe 0xbf6090/0xbf5330/0xbf53f0 -> 0xbf6c00): {_s} of "
                 "SimContext.transform (EAxis order Left, Right, Down, Up, Backward, Forward; frame 0 has Backward +z)")


def _xform(m, v, point: bool):
    v = _f(v)
    out = v @ m[:3, :3].T
    if point:
        out = out + m[:3, 3]
    return out.astype(F32)


def _masked(T: np.ndarray, bits: int) -> np.ndarray:
    """The part of a 4x4 transform a mask keeps: bit 0 translation, bit 1 rotation, bit 2 scale."""
    T = np.asarray(T, np.float64)
    lin = T[:3, :3]
    sc = np.linalg.norm(lin, axis=0)
    rot = lin / np.where(sc > 0, sc, 1)
    m = np.eye(4)
    if bits & 2:
        m[:3, :3] = rot * sc if bits & 4 else rot
    elif bits & 4:
        m[:3, :3] = np.diag(sc)
    if bits & 1:
        m[:3, 3] = T[:3, 3]
    return m


def xform_matrix(mask: int, now: np.ndarray, prev: np.ndarray | None = None) -> np.ndarray:
    """The matrix of xform*(v, mask) as D2R.exe 0xbfdd60 decodes the mask (one implementation for every name): bits
    0-2 the parts applied (1 translation, 2 rotation, 4 scale: 1 position only, 2 direction, 3 position, 7 all),
    bits 3-4 the source space, bits 5-6 the destination: 0 world, 1 world of the previous frame, 2 and 3 local (the
    effect's space; local to local, or a space to itself, is the identity). Corpus masks: l2w 17/18/19/23 (2 -> 0),
    w2l 66/67 (0 -> 2), 75 (1 -> 2), 107 (1 -> 3), w2w 9/10/11 (1 -> 0)."""
    bits, src, dst = mask & 7, (mask >> 3) & 3, (mask >> 5) & 3
    if src == dst or (src >> 1 and dst >> 1):
        return np.eye(4)
    prev = now if prev is None else prev
    to_local = np.eye(4) if src >> 1 else np.linalg.inv(_masked(now if src == 0 else prev, bits))
    from_local = np.eye(4) if dst >> 1 else _masked(now if dst == 0 else prev, bits)
    return from_local @ to_local


def xform_payload(mask: int, v, pos, ori=None) -> np.ndarray:
    """xform* local -> world in the spawn script of an event child whose payload has an XFormsPositions element
    (builtin kind 1; D2R.exe 0xbfdd60: source space 2/3, destination 0/1, spawner fields at ctx+0xb8): the local frame
    is the payload's position and, when the event also carries an XFormsOrientations element (kind 2), its rotation;
    never a scale (0xc019b0 per mask bit: 1 v + p, 2 q v q*, 3 q v q* + p). Previous-frame destinations read the same
    payload (no lerp: the payload is already at the child's sub-frame time)."""
    bits = mask & 3 if ori is not None else mask & 1
    n = len(pos)
    v = _f(v)
    out = np.array(np.broadcast_to(v[:, :3], (n, 3)) if len(v) != n else v[:, :3], F32)
    if bits & 2:
        out = qrot(np.asarray(ori, F32), out)
    if bits & 1:
        out = out + np.asarray(pos, F32)
    return out.astype(F32)


def _mask_of(mask) -> int:
    return int(as_base(mask, "i").reshape(-1)[0])


@engine("xform_l2w_f_masked_float3_int_pCtxS", "xform_l2w_d_masked_float3_int_pCtxS",
        "xform_w2l_f_masked_float3_int_pCtxS", "xform_w2w_f_masked_float3_int_pCtxS",
        "xform_w2w_d_masked_float3_int_pCtxS", "xform_l2l_f_masked_float3_int_pCtxS",
        "xform_w2l_d_masked_float3_int_pCtxS", "xform_l2l_d_masked_float3_int_pCtxS",
        note="xform(v, mask): the mask alone says what is done (D2R.exe 0xbfdd60, see vm.xform_matrix); the effect's "
             "transform is SimContext.transform, the previous frame's Env.prev_transform; local -> world in the spawn "
             "script of an event child uses the event payload's XFormsPositions/Orientations instead (vm.xform_payload)")
def _xf(ctx, v, mask, _c=None):
    k = _mask_of(mask)
    env = ctx.env
    now, prev = env.transform, getattr(env, "prev_transform", None)
    v = _f(v)
    xp = getattr(env, "xform_payload", None)
    if xp is not None and (k >> 3) & 2 and not (k >> 5) & 2:
        return xform_payload(k, v, *xp)
    fr = getattr(env, "birth_frac", None)
    if fr is None or prev is None or len(fr) != ctx.n or np.array_equal(np.asarray(now), np.asarray(prev)):
        m = xform_matrix(k, now, prev)
        return (v @ m[:3, :3].T.astype(F32) + m[:3, 3].astype(F32)).astype(F32)
    # per particle (D2R.exe 0xc001b0): the effect's transform lerped prev -> now at the particle's sub-frame time
    now, prev = np.asarray(now, np.float64), np.asarray(prev, np.float64)
    vv = np.broadcast_to(v, (ctx.n, v.shape[1])) if len(v) != ctx.n else v
    out = np.empty((ctx.n, 3), F32)
    for f in np.unique(fr):
        sel = fr == f
        m = xform_matrix(k, prev + (now - prev) * float(f), prev)
        out[sel] = vv[sel, :3] @ m[:3, :3].T.astype(F32) + m[:3, 3].astype(F32)
    return out


@engine("dxformdt_l2w_f_masked_float3_int_pCtxS", "dxformdt_w2l_f_masked_float3_int_pCtxS",
        "dxformdt_l2l_f_masked_float3_int_pCtxS", "dxformdt_w2w_f_masked_float3_int_pCtxS",
        note="d/dt of xform(v, mask) (D2R.exe 0xc02200 -> 0xbfc870.. and 0xc01ef0): the rotation/scale rows are "
             "(this frame's matrix - the previous frame's) / dt, the translation part is the effect's velocity "
             "(instance data, SimContext.velocity), not a finite difference; mask 1 (translation only) = the velocity")
def _dxf(ctx, v, mask, _c=None):
    env, k = ctx.env, _mask_of(mask)
    prev = getattr(env, "prev_transform", None)
    if prev is None:
        return np.zeros((1, 3), F32)
    now_m = xform_matrix(k, env.transform, prev)
    prev_m = xform_matrix(k, prev, prev)
    dt = np.asarray(env.dt, F32).reshape(-1, 1)
    v = _f(v)
    d = v @ (now_m - prev_m)[:3, :3].T.astype(F32) + (now_m - prev_m)[:3, 3].astype(F32)
    vel = getattr(env, "velocity", None)
    if vel is not None and k & 1 and not (k >> 5) & 2:      # translation into world space: the effect's velocity
        lin = np.asarray(vel, F32).reshape(1, 3)
        rot = v @ (now_m - prev_m)[:3, :3].T.astype(F32)
        with np.errstate(all="ignore"):
            return (np.where(dt > 0, rot / np.where(dt > 0, dt, 1), 0) + lin).astype(F32)
    with np.errstate(all="ignore"):
        return np.where(dt > 0, d / np.where(dt > 0, dt, 1), 0).astype(F32)


# view / scene ----------------------------------------------------------------------------------------------
def _view_axes(env):
    f = -np.asarray(env.camera, F32)
    f = f / (np.linalg.norm(f) or 1)
    side = np.cross([0, 1, 0], f)
    side = side / (np.linalg.norm(side) or 1)
    up = np.cross(f, side)
    return side.astype(F32), up.astype(F32), f.astype(F32)


@engine("view.position_int_SceneCtx", note="camera position (Env.camera)")
def _vpos(ctx, *_a):
    return np.asarray(ctx.env.camera, F32).reshape(1, 3)


@engine("view.direction_int_SceneCtx", "view.axisForward_int_SceneCtx", note="camera forward (towards the origin)")
def _vfwd(ctx, *_a):
    return _view_axes(ctx.env)[2].reshape(1, 3)


@engine("view.axisSide_int_SceneCtx")
def _vside(ctx, *_a):
    return _view_axes(ctx.env)[0].reshape(1, 3)


@engine("view.axisUp_int_SceneCtx")
def _vup(ctx, *_a):
    return _view_axes(ctx.env)[1].reshape(1, 3)


@engine("view.orientation_int_SceneCtx")
def _vori(ctx, *_a):
    s, u, f = _view_axes(ctx.env)
    return q_from_to(np.array([[0, 0, 1]], F32), f.reshape(1, 3))


@engine("view.distance_float3_SceneCtx", note="distance from the camera")
def _vdist(ctx, p, *_a):
    return np.linalg.norm(_f(p) - np.asarray(ctx.env.camera, F32), axis=1, keepdims=True).astype(F32)


NO_HIT = np.array([[1, 0, 0, np.inf]], F32)


def _collision_args(ctx, o, d, ln, en, mask):
    """(world, origin, direction, length, enabled, mask) of a scene.intersect* call: the world is `Env.collision` (a
    `collide.World`, set by the sim from SimContext.collision), else the default floor slab at `Env.ground_y`."""
    from hdkit.fx import collide
    o, d, ln = _f(o), _f(d), _f(ln)
    n = _rows(ctx, o, d, ln)
    world = getattr(ctx.env, "collision", None)
    if world is None:
        world = collide.floor_world(float(ctx.env.ground_y))
    en = as_base(en, "b") if en is not None else np.ones((1, 1), bool)
    m = int(np.asarray(as_base(mask, "i")).reshape(-1)[0]) if mask is not None else 0
    return (world, np.broadcast_to(o, (n, 3)), np.broadcast_to(d, (n, 3)), np.broadcast_to(ln[:, :1], (n, 1)),
            en, m)


@engine("scene.intersect_float3_float3_float_bool_int_SceneCtx",
        note="game: D2R.exe 0xbf3210 -> 0xbf18d0 -> PopcornPhysics::RayTracePacket (0x7e80c0, the game's physics world): "
             "sweep (r 0.01) from origin to origin + direction (the length is not used), query min(bsf(mask), 6) of "
             "D2Physics' table (mask 0 -> all); hit = (normal, 0) when 0 < length, miss (1, 0, 0, +inf), zeros for a "
             "false constant 'enabled'. World: Env.collision (hdkit.fx.collide), default the floor slab at ground_y")
def _intersect(ctx, o, d, ln, en=None, mask=None, *_a):
    from hdkit.fx import collide
    world, o, d, ln, en, m = _collision_args(ctx, o, d, ln, en, mask)
    out = collide.intersect(world, o, d, ln[:, 0], en, m)
    if ncomp(ctx.dst_type) == 3:                 # (no game script asks for a float3; kept for hand-written ones)
        hit = np.isfinite(out[:, 3:4])
        return (o + d * np.where(hit, out[:, 3:4], ln)).astype(F32)
    return out


@engine("scene.intersectExt_float3_float3_float_bool_int_SceneCtx",
        note="game: D2R.exe 0xbf3260 -> 0xbf2230: the same trace as scene.intersect; int4 raw bits: hit (normal.x, "
             "normal.y, distance 0 with normal.z's sign bit, -1), miss (1.0, 0, +inf, 0), zeros for a false constant "
             "'enabled' (hdkit.fx.collide.intersect_ext)")
def _intersect_ext(ctx, o=None, d=None, ln=None, en=None, mask=None, *_a):
    from hdkit.fx import collide
    if o is None:
        return np.zeros((1, max(1, ncomp(ctx.dst_type))), I32)
    world, o, d, ln, en, m = _collision_args(ctx, o, d, ln, en, mask)
    return collide.intersect_ext(world, o, d, ln[:, 0], en, m)


@engine("sim.lodQueryPoint_float3_pCtxS", note="LOD factor: 0 (full detail)")
def _lod(ctx, *_a):
    return np.zeros((1, 1), F32)


# particle ------------------------------------------------------------------------------------------------
@engine("self.kill_bool_pCtxS", note="kill the particles where the bool is true (after the script)")
def _kill(ctx, cond, _c=None):
    c = np.broadcast_to(as_base(cond, "b")[:, 0], (ctx.n,))
    ctx.env.killed |= c
    return None


@engine("self.readKillBit_pCtxS")
def _readkill(ctx, _c=None):
    return ctx.env.killed.reshape(-1, 1).copy()


# samplers (curves, shapes, vector fields: values from lane samplers through Env.sampler) ---------------------
def _lane_calls() -> dict:
    """samplers.ENGINE_CALLS of lane samplers (fn(ctx, sampler, *args)), when the module is there."""
    try:
        from . import samplers
    except ImportError:
        return {}
    return dict(getattr(samplers, "ENGINE_CALLS", {}))


LANE_CALLS = _lane_calls()


class LaneCtx:
    """The ctx samplers' engine calls expect: rand (a samplers.RandCtx), count, time."""
    __slots__ = ("rand", "count", "n", "time")

    def __init__(self, ctx: "CallCtx"):
        self.rand = getattr(ctx.env, "randctx", None)
        self.count = self.n = ctx.n
        self.time = getattr(ctx.env, "scene_time", ctx.env.t)    # the scene time (SceneCtx +0x108)


def _sampler_call(kind: str):
    def f(ctx, *args):
        smp = ctx.env.sampler(ctx.bound)
        vals = [a for a in args if not isinstance(a, Ctx)]
        if smp is not None:
            lane = LANE_CALLS.get(ctx.name)
            obj = getattr(smp, "obj", None)
            if lane is not None and obj is not None:
                lv = [np.broadcast_to(_f(x), (ctx.n, x.shape[1])) for x in vals]
                lv = [v[:, 0] if v.shape[1] == 1 else v for v in lv]
                out = lane(LaneCtx(ctx), obj, *lv)
                if out is not None:
                    return np.asarray(out)
            out = smp(kind, ctx, *vals)
            if out is not None:
                return out
        ctx.env.stats["sampler_stub"][ctx.name] += 1
        return ctx.zeros()
    return f


for _n in ("samplerCurve1:sample_float", "samplerCurve2:sample_float", "samplerCurve3:sample_float",
           "samplerCurve4:sample_float"):
    ENGINE[_n] = _sampler_call("curve")
for _n in ("samplerCurve1:derive_float_pCtxI", "samplerCurve1:derive_float"):
    ENGINE[_n] = _sampler_call("curve_derive")
for _n in ("samplerVectorField:sample_float3_float_SceneCtx", "samplerVectorField:sample_float3_SceneCtx"):
    ENGINE[_n] = _sampler_call("vectorfield")
for _n in ("samplerShape:samplePosition_RandCtx", "samplerShape:samplePosition_RandCtx_pCtxI",
           "samplerShape:samplePosition_int3", "samplerShape:samplePosition_int3_pCtxI",
           "samplerShape:sampleNormal_int3", "samplerShape:sampleNormal_int3_pCtxI",
           "samplerShape:sampleTangent_int3_pCtxI", "samplerShape:sampleTexcoord0_int3",
           "samplerShape:samplePCoords_RandCtx", "samplerShape:samplePCoords_RandCtx_pCtxI",
           "samplerShape:projectPCoords_float3", "samplerShape:sampleSurfacePCoordsFromUV_float2"):
    ENGINE[_n] = _sampler_call("shape:" + _n.split(":")[1].split("_")[0])


# events: spawners, payloads, kicks ------------------------------------------------------------------------
# An event descriptor (int3) is (state.x, state.y, count): `generate` returns the spawner state to keep (the script
# stores .xy back) and how many events fire this frame for each particle (D2R.exe keeps a record id in .z and the
# per-event data aside; here the count is in .z and the sub-frame times of the children in `env.emissions`).
def _bits_f(i):
    return np.asarray(i, I32).view(F32)


def _bits_i(f):
    return np.asarray(f, F32).view(I32)


def _finite(x):
    return np.where(np.isfinite(x), x, 0).astype(F32)


@engine("generate_int2_float_float_float_float_pCtxS", "generate_int2_float_float_float_float_pCtxS_",
        note="generate(state, amount, divisor, a, b) as D2R.exe 0xc526b0/0xc529f0: n = amount / divisor (divisor > 0, "
             "else nothing), acc = state.x + n; when acc > 1e-10: count = floor(acc), state.x = acc - count, state.y "
             "+= count; child k (0..count-1) sits at f = min(1, (k + 1 - state.x_old) / n) of the window [a, b] of "
             "the frame (its time = lerp(lerp(t0, t1, a), lerp(t0, t1, b), f)); non-finite inputs read as 0")
def _generate(ctx, state, amount, divisor, a, b, _c=None):
    st = np.broadcast_to(as_base(state, "i"), (ctx.n, 2))
    amount, divisor, a, b = (_finite(np.broadcast_to(_f(x), (ctx.n, 1))[:, 0]) for x in (amount, divisor, a, b))
    acc0 = _finite(_bits_f(np.ascontiguousarray(st[:, 0])))
    with np.errstate(all="ignore"):
        n = np.where(divisor > 0, amount / np.where(divisor > 0, divisor, 1), 0).astype(F32)
    acc = np.maximum(acc0 + n, 0).astype(F32)
    fire = (divisor > 0) & (acc > F32(1e-10))
    cnt = np.where(fire, np.floor(np.minimum(acc, 1e6)), 0).astype(I32)
    rest = np.where(fire, acc - np.floor(acc), acc0).astype(F32)
    out = np.stack([_bits_i(rest), st[:, 1] + cnt, cnt], axis=1).astype(I32)
    _emission(ctx.env, out, {"acc0": acc0, "n": n, "a": a, "b": b})
    return out


def _emission(env, desc, info) -> None:
    em = getattr(env, "emissions", None)
    if em is not None:
        em.append((desc, info))


def child_fracs(info: dict | None, counts: np.ndarray) -> np.ndarray | None:
    """Sub-frame time in [0, 1] of every child of a kick (particle-major, as Sim.kick repeats them), from the
    emission that made the event descriptor: generate's window and accumulator, trigger's clamped float."""
    if not info:
        return None
    idx = np.repeat(np.arange(len(counts)), counts)
    if "frac" in info:
        return np.broadcast_to(info["frac"], (len(counts),))[idx].astype(F32)
    k = np.arange(len(idx)) - np.repeat(np.cumsum(counts) - counts, counts)
    n = info["n"][idx]
    with np.errstate(all="ignore"):
        f = np.where(n > F32(1e-10), np.minimum(1, (k + 1 - info["acc0"][idx]) / np.where(n > 0, n, 1)), 1)
    return (info["a"][idx] + (info["b"][idx] - info["a"][idx]) * f).astype(F32)


@engine("samplerEventStream:generate_int2_float_float_float_float_pCtxS",
        note="events of the stream sampler with time <= t1 not yet fired (state.x = next event)")
def _gen_stream(ctx, state, t0, t1, _off, _dt, _c=None):
    st = np.broadcast_to(as_base(state, "i"), (ctx.n, 2)).copy()
    times = None
    smp = ctx.env.sampler(ctx.bound)
    if smp is not None:
        times = smp("eventstream", ctx)
    if times is None:
        ctx.env.stats["sampler_stub"][ctx.name] += 1
        times = np.zeros(0, F32)
    t1 = np.broadcast_to(_f(t1), (ctx.n, 1))[:, 0]
    upto = np.searchsorted(np.sort(times), t1, side="right").astype(I32)
    cnt = np.maximum(upto - st[:, 0], 0).astype(I32)
    return np.stack([np.maximum(upto, st[:, 0]), st[:, 1] + cnt, cnt], axis=1).astype(I32)


@engine("samplerEventStream:duration_pCtxI", note="time of the stream's last event")
def _stream_dur(ctx, _c=None):
    smp = ctx.env.sampler(ctx.bound)
    times = smp("eventstream", ctx) if smp is not None else None
    return np.full((1, 1), float(np.max(times)) if times is not None and len(times) else 0.0, F32)


@engine("trigger_bool_float_pCtxS",
        note="trigger(cond, t) as D2R.exe 0xc53580: one event per particle where cond != 0, at sub-frame time "
             "clamp(t, 0, 1) (the child's time = lerp(t0, t1, t))")
def _trigger(ctx, cond, t, _c=None):
    c = np.broadcast_to(as_base(cond, "b")[:, 0], (ctx.n,)).astype(I32)
    z = np.zeros(ctx.n, I32)
    out = np.stack([z, z, c], axis=1)
    _emission(ctx.env, out, {"frac": np.clip(_finite(np.broadcast_to(_f(t), (ctx.n, 1))[:, 0]), 0, 1)})
    return out


@dataclass
class Payload:
    desc: np.ndarray                  # (n, 3) int3 event descriptor (count in z)
    elements: dict = field(default_factory=dict)   # element id -> (v0, v1, build tag)
    info: dict | None = None          # the emission (generate/trigger) that made desc: children's sub-frame times


def _handle(env, obj) -> np.ndarray:
    env.handles.append(obj)
    return np.full((1, 1), len(env.handles) - 1, I32)


def _deref(env, h):
    i = int(as_base(h, "i").reshape(-1)[0])
    return env.handles[i] if 0 <= i < len(env.handles) else None


@engine("particleEvent:initPayload_int_int3_pCtxS", note="a payload for an event descriptor; returns its handle")
def _init_payload(ctx, _layout, desc, _c=None):
    d = np.broadcast_to(as_base(desc, "i"), (ctx.n, 3)).copy()
    info = next((i for e, i in reversed(getattr(ctx.env, "emissions", [])) if e.shape == d.shape and
                 np.array_equal(e, d)), None)
    return _handle(ctx.env, Payload(d, info=info))


def _build(ctx, desc, v0, v1, eid, *_c, t0=None, t1=None):
    """eid = semantic << 8 | interpolation (D2R.exe 0xc58ca0): semantic (payload field 4) 1 position, 2 orientation,
    3 count; interpolation (low byte) 0 none (every child gets v0), 1/2 linear v0 -> v1, 3 cubic Hermite with
    tangents (v1 - v0) + t0 and (v1 - v0) + t1 (buildPayloadElementD)."""
    e = int(as_base(eid, "i").reshape(-1)[0])
    pp = (lambda v: v if v is None or isinstance(v, Ctx) else ctx.env.per_particle(np.asarray(v)))
    return _handle(ctx.env, ("elem", e, pp(v0), pp(v1), pp(t0), pp(t1)))


for _n in ("buildPayloadElement_int3_float3_float3_int_pCtxS", "buildPayloadElement_int3_float_float_int_pCtxS",
           "buildPayloadElement_int3_orientation_orientation_int_pCtxS", "buildPayloadElement_int3_int_int_int_pCtxS",
           "buildPayloadElement_int3_float4_float4_int_pCtxS", "buildPayloadElement_int3_float2_float2_int_pCtxS"):
    ENGINE[_n] = _build
    NOTES[_n] = ("payload element (D2R.exe 0xc58ca0): (desc, v0, v1, eid), eid = semantic << 8 | interpolation "
                 "(0 none: v0, 1/2 linear v0 -> v1 at each child's sub-frame time); vm._build")


@engine("buildPayloadElementD_int3_float3_float3_float3_float3_int_pCtxS",
        note="element with tangents (D2R.exe 0xbf2d40 -> 0xc544d0 -> 0xc58ca0): (desc, v0, v1, t0, t1, eid); "
             "interpolation 3 = Hermite v0 + h01 (v1 - v0) + h10 ((v1 - v0) + t0) + h11 ((v1 - v0) + t1)")
def _buildD(ctx, desc, v0, v1, t0, t1, eid, *_c):
    return _build(ctx, desc, v0, v1, eid, t0=t0, t1=t1)


@engine("particleEvent:appendPayload_int_int_int_pCtxS",
        note="put an element in slot k of a payload: element k of the event's payload list (CLayerCompileCacheEvent "
             "field 2); a child reads it by name (routing.py); returns the payload")
def _append(ctx, payload, slot, elem, _c=None):
    p, e = _deref(ctx.env, payload), _deref(ctx.env, elem)
    if isinstance(p, Payload) and isinstance(e, tuple):
        p.elements[int(as_base(slot, "i").reshape(-1)[0])] = (e[2], e[3], e[1], *e[4:])
    return as_base(payload, "i")


@engine("particleEvent:kick_int_RandCtx_pCtxS", note="fire the bound event: desc.z events per particle")
def _kick(ctx, payload, _r=None, _c=None):
    p = _deref(ctx.env, payload)
    if isinstance(p, Payload):
        counts = p.desc[:, 2].astype(np.int64).clip(0, None)
        ctx.env.kick(ctx.bound, counts, p.elements, child_fracs(p.info, counts))
    return None


def _extract(k: int, base: str):
    def f(ctx, eid, _c=None):
        e = int(as_base(eid, "i").reshape(-1)[0])
        v = ctx.env.payload(e, k)
        if v is None:
            ctx.env.stats["doubtful"]["payload_missing:" + ctx.name] += 1
            return ctx.zeros()
        return v
    return f


for _n, _k, _b in (("extractPayloadElementF1_int_pCtxS", 1, "f"), ("extractPayloadElementF2_int_pCtxS", 2, "f"),
                   ("extractPayloadElementF3_int_pCtxS", 3, "f"), ("extractPayloadElementF4_int_pCtxS", 4, "f"),
                   ("extractPayloadElementI1_int_pCtxS", 1, "i"), ("extractPayloadElementO_int_pCtxS", 4, "f")):
    ENGINE[_n] = _extract(_k, _b)
    NOTES[_n] = "the payload element of the event that spawned the particle (interpolated at its sub-frame time)"


# D2R's own hooks (_pksi_vv*): lights, decals, model instances, wind, attenuation -------------------------------
def _alloc(kind):
    def f(ctx, *_a):
        return ctx.env.alloc(kind) if hasattr(ctx.env, "alloc") else np.zeros((ctx.n, 2), I32)
    return f


def _update(kind):
    def f(ctx, h, release, *rest):
        if hasattr(ctx.env, "update_side"):
            ctx.env.update_side(kind, as_base(h, "i"), ~as_base(release, "b"), rest)
        return None
    return f


def _kill_side(kind):
    def f(ctx, h, *_a):
        return np.zeros((ctx.n, 1), I32)        # 0: the game has not dropped it (1 -> the script kills the particle)
    return f


ENGINE["_pksi_vvAllocateLight_SI_pCtxS"] = _alloc("light")
ENGINE["_pksi_vvAllocateDecal_SI_bool_pCtxS"] = _alloc("decal")
ENGINE["_pksi_vvCreateAnimatedModelInstance_SI_pCtxI"] = _alloc("model")
ENGINE["_pksi_vvUpdateLight_SI_int2_bool_pCtxS"] = _update("light")
ENGINE["_pksi_vvUpdateDecal_SI_int2_bool_pCtxS"] = _update("decal")
ENGINE["_pksi_vvUpdateTeleportLight_SI_int2_bool_bool_float3_pCtxS"] = _update("light")
ENGINE["_pksi_vvKillLightImmediate_SI_int2_pCtxS"] = _kill_side("light")
ENGINE["_pksi_vvKillDecalImmediate_SI_int2_pCtxS"] = _kill_side("decal")
ENGINE["_pksi_vvDestroyAnimatedModelInstance_SI_int4_bool_pCtxI"] = _kill_side("model")
for _n in [k for k in ENGINE if k.startswith("_pksi_vv") and k != "_pksi_vvHasTeleported_SI"]:
    NOTES[_n] = ("game: D2R hook (D2Render sim interfaces, D2R.exe 0xe14060..0xe17230 register them; lights "
                 "0x1001400/0x1001cf0/0x1001880, decals 0x1000eb0/0x1001af0/0x1001610 on the game's light/decal "
                 "manager, at most 1024 slots): Allocate returns the handle int2 (id, slot; -1, -1 when full), "
                 "Update(handle, IsDead) keeps it (IsDead false) or frees it at once (true), "
                 "Kill*Immediate(handle) is 1 when the game no longer lists the handle (the script then kills the "
                 "particle); here every allocated handle stays listed (0). Colour, power and range are the "
                 "renderer's inputs (sim.side_record)")


@engine("_pksi_vvGetVfxAttenuation_SI_float3",
        note="game: D2R.exe 0xd98f40: the game's callback (0xe26d0, set at 0x7ef6b) per point: ray down to the floor, "
             "tile cell -> byte of the 48x48 visibility/light map around the player / 255; without the callback "
             "1.0 (here)")
def _atten(ctx, *_a):
    return np.ones((1, 1), F32)


@engine("_pksi_vvWindDirection_SI_float3",
        note="game: D2R.exe 0x1000d80 -> 0xe5ff20: the game's wind (direction and strength globals of the area/weather, "
             "gusts from sines of time and x + z, noise, scaled by clamp(y / 30, 0.1, 1)); no game wind here: 0")
def _wind(ctx, *_a):
    return np.zeros((1, 3), F32)


# ---------------------------------------------------------------------------------------------------- helpers
def fold(script: cb.Script, streams: dict | None = None, n: int = 1, **kw) -> dict:
    """Run a script once against the default `Env` (constant folding): returns the streams it stored."""
    env = Env(script, n, **kw)
    env.streams.update(streams or {})
    run(script, n, env)
    return env.streams


def coverage_of(script: cb.Script) -> dict:
    """Which engine calls of a script are implemented (not a stub)."""
    names = set(script.fn_names or [])
    return {"calls": sorted(names), "missing": sorted(n for n in names if n not in ENGINE),
            "doubtful": sorted(n for n in names if NOTES.get(n, "").startswith("doubtful"))}


def mangle_check() -> list[str]:
    """Engine names registered more than once under different functions (sanity)."""
    return [n for n in ENGINE if not isinstance(n, str)]


_ = struct  # (kept for callers that build constants with struct)
