"""Behavioural check of the script language (fx/lang) with the interpreter (fx/vm, fx/sim).

    python -m hdkit.fx langvm <dir|file>... [--every 1] [--n 64] [--effects 20] [--t 1.0] [--json out.json]

Every script that `fx.lang` decompiles is compiled back (`base` = the original) and both are run by `vm.run` on the same
particles: random stream values of each external's type, the default context, and deterministic stand-ins for the
random engine calls (a hash of the particle and the arguments, so a reordered call draws the same numbers). The stores,
kills and event kicks must match (floats to 1e-5 relative, NaN = NaN). With `--effects N`, N whole effects also run
in `Sim` with every script replaced, and the particle counts and streams of the two runs are compared.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

from . import cbvm as cb
from . import vm

F32 = np.float32
RANDOM = ("rand_float_float_RandCtx", "rand_float2_float2_RandCtx", "rand_float3_float3_RandCtx",
          "rand_float4_float4_RandCtx", "vrand_RandCtx", "vrand_float_float_RandCtx")


def _hash01(n: int, k: int, salt: float) -> np.ndarray:
    i = np.arange(n, dtype=np.float64).reshape(-1, 1) + 1
    j = np.arange(k, dtype=np.float64).reshape(1, -1) + 1
    return ((np.sin(i * 12.9898 + j * 78.233 + salt * 37.719) * 43758.5453) % 1.0).astype(F32)


def _salt(*vals) -> float:
    return float(sum(float(np.nansum(np.asarray(v, np.float64))) for v in vals if not isinstance(v, vm.Ctx)))


def _det_rand(ctx, a, b, _r=None):
    a, b = vm.as_base(a, "f"), vm.as_base(b, "f")
    k = max(a.shape[1], b.shape[1])
    return a + (b - a) * _hash01(ctx.n, k, _salt(a, b))


def _det_vrand(ctx, *args):
    v = _hash01(ctx.n, 3, _salt(*args) + 0.5) * 2 - 1
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
    if len(args) >= 2 and not isinstance(args[0], vm.Ctx):
        lo, hi = vm.as_base(args[0], "f"), vm.as_base(args[1], "f")
        v = v * (lo + (hi - lo) * _hash01(ctx.n, 1, _salt(lo, hi) + 0.25))
    return v.astype(F32)


class deterministic:
    """Context manager: the random engine calls draw from `_hash01` instead of the Env's generator."""

    def __enter__(self):
        self.saved = {k: vm.ENGINE.get(k) for k in RANDOM}
        for k in RANDOM:
            vm.ENGINE[k] = _det_vrand if k.startswith("vrand") else _det_rand
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                vm.ENGINE.pop(k, None)
            else:
                vm.ENGINE[k] = v
        return False


def _inputs(s: cb.Script, n: int, rng) -> dict:
    out = {}
    for name, d in zip(s.ext_names, s.ext_desc):
        if name is None or d[3] != 0 or d[1] == cb.NONE:
            continue
        t = d[1] + 1
        base, k = cb.type_base(t), max(1, vm.ncomp(t))
        if base == "f":
            out[name] = (rng.random((n, k), dtype=F32) * 4 - 2).astype(F32)
        elif base == "b":
            out[name] = rng.random((n, k)) < 0.5
        elif base == "i":
            out[name] = rng.integers(-50, 50, (n, k)).astype(np.int32)
    if "self.lifeRatio" in out:
        out["self.lifeRatio"] = np.abs(out["self.lifeRatio"]) / 2
    return out


def _run(s: cb.Script, streams: dict, n: int):
    env = vm.Env(s, n, t=0.75, dt=1 / 30, attrs={"Fade": [1.0], "MonsterFade": [1.0]})
    env.streams.update({k: v.copy() for k, v in streams.items()})
    with np.errstate(all="ignore"):
        vm.run(s, n, env)
    return env


def _same(a, b) -> bool:
    if isinstance(a, vm.Ctx) or isinstance(b, vm.Ctx):
        return True
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    if a.dtype.kind != "f" and b.dtype.kind != "f":
        return bool(np.array_equal(a, b))
    a, b = a.astype(np.float64), b.astype(np.float64)
    both_nan = np.isnan(a) & np.isnan(b)
    with np.errstate(all="ignore"):
        ok = np.isclose(a, b, rtol=1e-5, atol=1e-5) | both_nan | ((a == b) & np.isinf(a))
    return bool(ok.all())


def script_behaviour(a: cb.Script, b: cb.Script, n: int = 64, seed: int = 0) -> list[str]:
    """How `b` behaves differently from `a` on the same inputs (empty: the same)."""
    streams = _inputs(a, n, np.random.default_rng(seed))
    with deterministic():
        ea, eb = _run(a, streams, n), _run(b, streams, n)
    out = []
    for k in sorted(set(ea.streams) | set(eb.streams)):
        if not _same(ea.streams.get(k), eb.streams.get(k)):
            out.append(f"stream {k} differs")
    if not np.array_equal(ea.killed, eb.killed):
        out.append("kills differ")
    if ea.stats["kicks"] != eb.stats["kicks"]:
        out.append(f"kicks differ: {dict(ea.stats['kicks'])} != {dict(eb.stats['kicks'])}")
    return out


def roundtrip(s: cb.Script):
    """The script decompiled and compiled back, or the reason it cannot be (a string)."""
    from . import lang
    try:
        src = lang.decompile(s)
    except Exception as e:  # noqa: BLE001
        return f"decompile: {type(e).__name__}: {e}"[:200]
    try:
        b = lang.compile(src, base=s)
    except Exception as e:  # noqa: BLE001
        return f"compile: {type(e).__name__}: {e}"[:200]
    b.obj = s.obj
    return b


def effect_behaviour(data: bytes, t: float = 1.0, seed: int = 0) -> list[str]:
    """Run the effect with its scripts and with every script round-tripped through fx/lang; what differs."""
    from . import routing as rt
    from . import sim as S
    r1 = rt.load(data)
    r2 = rt.load(data)
    swapped = {}
    for slot in r2.slots:
        for attr in ("spawn", "evolve", "newborn"):
            s = getattr(slot, attr)
            if s is None:
                continue
            if id(s) not in swapped:
                b = roundtrip(s)
                swapped[id(s)] = b if isinstance(b, cb.Script) else s
            setattr(slot, attr, swapped[id(s)])
    out = []
    with deterministic():
        a, b = S.Sim(r1, seed=seed), S.Sim(r2, seed=seed)
        a.step(t)
        b.step(t)
    sa, sb = a.state(), b.state()
    for name in sa.layers:
        la, lb = sa.layers[name], sb.layers.get(name)
        if lb is None or la.count != lb.count:
            out.append(f"{name}: {la.count} != {None if lb is None else lb.count} particles")
            continue
        for k, v in la.fields.items():
            if not _same(v, lb.fields.get(k)):
                out.append(f"{name}.{k} differs")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx langvm", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--every", type=int, default=1, help="every k-th file")
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--effects", type=int, default=0)
    ap.add_argument("--t", type=float, default=1.0)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    files = [f for p in map(Path, a.paths) for f in (sorted(p.rglob("*.particles")) if p.is_dir() else [p])]
    files = files[::max(1, a.every)]
    res = collections.Counter()
    why = collections.Counter()
    bad: list = []
    for f in files:
        for s in cb.scripts(cb.hp.ParticleFile(f.read_bytes())):
            b = roundtrip(s)
            if not isinstance(b, cb.Script):
                res["not round-tripped"] += 1
                why[b.split(":")[0] + ": " + b.split(":", 2)[-1].strip()[:60]] += 1
                continue
            d = script_behaviour(s, b, a.n)
            res["same" if not d else "differ"] += 1
            if d:
                bad.append({"file": str(f), "obj": s.obj + 1, "why": d[:4]})
    eff = []
    for f in files[:a.effects]:
        d = effect_behaviour(f.read_bytes(), a.t)
        eff.append({"file": str(f), "differ": d[:6]})
    print(f"{len(files)} files: {dict(res)}")
    for k, v in why.most_common(8):
        print(f"  not round-tripped {v}: {k}")
    for x in bad[:20]:
        print(f"  differ: {x['file']} object {x['obj']}: {'; '.join(x['why'])}")
    if eff:
        print(f"effects in Sim: {sum(1 for e in eff if not e['differ'])}/{len(eff)} the same")
        for e in eff:
            if e["differ"]:
                print(f"  {e['file']}: {'; '.join(e['differ'])}")
    if a.json:
        Path(a.json).write_text(json.dumps({"scripts": dict(res), "not_round_tripped": dict(why), "differ": bad,
                                            "effects": eff}, indent=1))
    return 0 if not bad and all(not e["differ"] for e in eff) else 1


if __name__ == "__main__":
    sys.exit(main())
