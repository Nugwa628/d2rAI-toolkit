"""Command line of hdkit.fx.

  python -m hdkit.fx sim <file> [--t 2.0] [--seed 0] [--attr Fade=1] [--json]
        run the effect to time t: particle counts and per-stream min/mean/max per layer, events, counters
  python -m hdkit.fx vmcov <dir|file>... [--run] [--t 1.0] [--json out.json]
        opcodes and engine calls over the scripts: which are implemented, which scripts (and with --run, which effects)
        run with no stub call and no error
  python -m hdkit.fx dis <file> [--obj N]      the CBVM listing (hdkit.fx.cbvm)

A <file> that does not exist is looked up under the roots of HDKIT_VIEW_ROOTS (`name=folder`, os.pathsep separated).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

from . import cbvm as cb
from . import vm


def resolve(p: str) -> Path:
    f = Path(p)
    if f.exists():
        return f
    for item in filter(None, os.environ.get("HDKIT_VIEW_ROOTS", "").split(os.pathsep)):
        root = Path(item.split("=", 1)[-1])
        for cand in (root / p, root / "hd" / p, root / "hd" / "vfx" / "particles" / p):
            if cand.exists():
                return cand
    raise SystemExit(f"not found: {p} (and not under HDKIT_VIEW_ROOTS)")


def files_of(paths) -> list[Path]:
    out = []
    for p in paths:
        f = resolve(p)
        out += sorted(f.rglob("*.particles")) if f.is_dir() else [f]
    return out


def cmd_sim(a) -> int:
    from . import sim as S
    attrs = {}
    for kv in a.attr or []:
        k, _, v = kv.partition("=")
        attrs[k] = [float(x) for x in v.split(",")]
    s = S.Sim(resolve(a.file), seed=a.seed, attrs=attrs)
    s.step(a.t)
    out = S.summary(s)
    if a.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(f"{out['path']}  t={out['t']}")
        for name, L in out["layers"].items():
            print(f"  {name:28s} {L['count']:6d} particles")
            for k, f in L["fields"].items():
                print(f"      {k:16s} min {f['min']} mean {f['mean']} max {f['max']}")
        print("  events", dict(out["events"]))
        for k, v in out.get("stats", {}).items():
            if k not in ("calls", "scripts_run"):
                print(f"  {k}: {v}")
    return 0


def coverage(files, run: bool = False, t: float = 1.0) -> dict:
    ops, calls, missing = collections.Counter(), collections.Counter(), collections.Counter()
    n_scripts = full = 0
    per_file = {}
    for f in files:
        data = f.read_bytes()
        if not data:
            continue
        ss = cb.scripts(cb.hp.ParticleFile(data))
        bad = set()
        for s in ss:
            n_scripts += 1
            for i in s.code:
                ops[i.name if i.op != 0x70 else f"mop.{cb.MOP[i.imm[0]]}"] += 1
            c = vm.coverage_of(s)
            for name in c["calls"]:
                calls[name] += 1
            for name in c["missing"]:
                missing[name] += 1
                bad.add(name)
            full += not c["missing"]
        rec = {"scripts": len(ss), "missing_calls": sorted(bad)}
        if run:
            from . import sim as S
            try:
                sim = S.Sim(data, seed=0)
                sim.step(t)
                st = sim.stats
                rec.update(particles=sum(L.n for L in sim.layers), stubs=dict(st["stub_calls"]),
                           errors=dict(st["errors"]), sampler_stub=dict(st["sampler_stub"]))
                rec["runs"] = not st["stub_calls"] and not st["errors"]
            except Exception as e:  # noqa: BLE001
                rec.update(runs=False, crash=f"{type(e).__name__}: {e}"[:200])
        per_file[str(f)] = rec
    out = {"files": len(per_file), "scripts": n_scripts, "scripts_fully_implemented": full,
           "opcodes": dict(ops.most_common()), "calls": dict(calls.most_common()),
           "missing_calls": dict(missing.most_common()),
           "doubtful_calls": sorted(n for n in calls if vm.NOTES.get(n, "").startswith("doubtful"))}
    if run:
        out["effects_run_clean"] = sum(1 for r in per_file.values() if r.get("runs"))
        errs = collections.Counter()
        for r in per_file.values():
            for e, n in (r.get("errors") or {}).items():
                errs[e] += n
            if r.get("crash"):
                errs[r["crash"]] += 1
        out["errors"] = dict(errs.most_common(40))
    out["per_file"] = per_file
    return out


def cmd_coverage(a) -> int:
    out = coverage(files_of(a.paths), a.run, a.t)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))
    print(f"{out['files']} files, {out['scripts']} scripts, {out['scripts_fully_implemented']} with every engine "
          f"call implemented")
    if a.run:
        print(f"effects that run {a.t}s with no stub call and no error: {out['effects_run_clean']}/{out['files']}")
        for e, n in list(out["errors"].items())[:15]:
            print(f"  error x{n}: {e}")
    print("opcodes:", ", ".join(f"{k} {v}" for k, v in out["opcodes"].items()))
    print("missing calls:", out["missing_calls"] or "none")
    print("doubtful calls:", len(out["doubtful_calls"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sim")
    a.add_argument("file")
    a.add_argument("--t", type=float, default=2.0)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--attr", action="append", help="NAME=v[,v...] effect attribute")
    a.add_argument("--json", action="store_true")
    a = sub.add_parser("vmcov")
    a.add_argument("paths", nargs="+")
    a.add_argument("--run", action="store_true", help="also run every effect for --t seconds")
    a.add_argument("--t", type=float, default=1.0)
    a.add_argument("--json")
    a = sub.add_parser("dis")
    a.add_argument("file")
    a.add_argument("--obj", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "sim":
        return cmd_sim(args)
    if args.cmd == "vmcov":
        return cmd_coverage(args)
    return cb.main(["dis", str(resolve(args.file))] + (["--obj", str(args.obj)] if args.obj else []))
