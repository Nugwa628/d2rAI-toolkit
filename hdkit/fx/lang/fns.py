"""Engine functions: mangled names, overloads, argument kinds (docs/hdfx-lang.md section 5)."""
from __future__ import annotations

from dataclasses import dataclass

from .isa import C, Q, U, S

CTX = {"pCtxS": "ParticleContextS", "pCtxI": "ParticleContextI", "RandCtx": "RandContext",
       "SceneCtx": "SceneContext"}
PARAM_TYPES = {f"{b}{n}" for b in ("float", "int", "bool") for n in ("", "2", "3", "4")} | {"orientation"}
# methods: the object's kind (as an `extern` kind) -> mangled prefix
METHOD_PREFIX = {"curve1": "samplerCurve1", "curve2": "samplerCurve2", "curve3": "samplerCurve3",
                 "curve4": "samplerCurve4", "shape": "samplerShape", "vfield": "samplerVectorField",
                 "eventstream": "samplerEventStream", "event": "particleEvent"}
PREFIX_KIND = {v: k for k, v in METHOD_PREFIX.items()}
IMPURE_PREFIXES = ("rand", "vrand", "self.kill", "particleEvent:", "buildPayload", "trigger", "_pksi_vv",
                   "samplerEventStream:generate", "generate_", "samplerShape:sample", "samplerShape:samplePCoords")


def split_mangled(name: str) -> tuple[str, list[str], list[str]]:
    """`xform_l2w_f_masked_float3_int_pCtxS` -> ('xform_l2w_f_masked', ['float3', 'int'], ['pCtxS'])."""
    parts = name.split("_")
    k = len(parts)
    while k > 1 and (parts[k - 1].replace("orientation", "orientation") in PARAM_TYPES or parts[k - 1] in CTX):
        k -= 1
    tail = parts[k:]
    if k == 0 or not "_".join(parts[:k]):
        k, tail = len(parts), []
    return "_".join(parts[:k]), [t for t in tail if t in PARAM_TYPES], [t for t in tail if t in CTX]


@dataclass
class Fn:
    name: str                 # mangled
    base: str                 # without the suffix (e.g. `rand`, `samplerCurve1:sample`)
    params: list[str]
    ctxs: list[str]
    ret: str | None
    imm: list[int]            # parameters passed as immediates (argument kind 0)

    @property
    def method(self) -> str | None:
        return self.base.split(":")[0] if ":" in self.base else None

    @property
    def short(self) -> str:   # the surface name (method name for methods)
        return self.base.split(":", 1)[1] if ":" in self.base else self.base

    @property
    def impure(self) -> bool:
        return self.name.startswith(IMPURE_PREFIXES) or self.ret is None


def _table():
    from .fns_data import FNS
    out = {}
    for name, (ret, imm, _n) in FNS.items():
        b, p, c = split_mangled(name)
        out[name] = Fn(name, b, p, c, ret, list(imm))
    return out


TABLE: dict[str, Fn] = _table()


def lookup(name: str, ret: str | None = None) -> Fn:
    """A function by mangled name; one not in the table gets its signature from the name (`ret` from the caller)."""
    f = TABLE.get(name)
    if f is not None:
        return f
    b, p, c = split_mangled(name)
    return Fn(name, b, p, c, ret, [])


def overloads(base: str) -> list[Fn]:
    return [f for f in TABLE.values() if f.base == base]


def arg_kind(param: str | None, cls: int, t: str, imm: bool) -> int:
    """The kind byte of a call argument (docs section 1)."""
    if param is None:                      # a context
        return 0 if cls == Q else 8
    if imm:
        return 0
    return 8 | (0x10 if cls in (U, S) else 0) | (2 if t == "float" else 0)


def result_class(classes: list[int]) -> int:
    m = max([Q] + [c for c in classes if c != C], key=lambda c: {C: 0, Q: 1, U: 2, S: 3}[c])
    return m


def call_flags(ret: str | None, cls: int) -> int:
    if ret is None:
        return 0
    return 4 if cls == Q else 6
