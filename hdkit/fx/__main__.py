"""`python -m hdkit.fx <command> ...`; lanes add their commands to COMMANDS.

Format lane (graph.py/pfile.py):

  info <file>                      layers, renderers (kind, material, on/off, files), samplers, attributes
  dump <file> [-o out.json]        the effect graph as JSON (graph.Effect.to_json)
  survey <root> [-o survey.json]   per file: classes, renderer kinds/materials/features, CBVM ops, engine calls
  roundtrip <root>                 every file read and written back byte for byte, references checked, graph loaded
Context lane: `context ...`; coverage lane: `coverage ...` (see those modules).
Vm lane (cli.py): `sim <file> --t 2 --json`, `vmcov <dir> [--run]`, `dis <file>`; `langvm <dir>` (vmcheck.py).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

from . import graph
from .pfile import PFile


def files(paths) -> list[tuple[Path, Path]]:
    """(root, file) of every .particles file named or under the folders named."""
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += [(p, f) for f in sorted(p.rglob("*.particles"))]
        else:
            out.append((p.parent, p))
    return out


def info(eff: graph.Effect) -> list[str]:
    lines = [f"{eff.path}: PopcornFX {eff.version}, {eff.objects} objects, platforms {', '.join(eff.platforms)}"
             f" (showing {eff.platform})"]
    for a in eff.attributes:
        lines.append(f"attribute {a.name}: {a.type} default {a.default}")
    for lay in eff.layers:
        by = ", ".join(n or "start" for n in lay.spawn.get("by", [])) if lay.spawn else ""
        lines.append(f"layer slot {lay.slot} '{lay.name}' (object {lay.obj}): {len(lay.fields)} fields, "
                     f"scripts {lay.scripts}, spawned by [{by}], children {lay.children}")
        for s in lay.samplers:
            lines.append(f"    sampler {s}")
        for r in lay.renderers:
            feats = [k for k, p in r.props.items() if p["type"] == "feature" and p["value"]]
            on = "on" if r.enabled else "OFF"
            lines.append(f"    renderer {r.index} (object {r.obj}): {r.kind}, {r.material}, {on}"
                         f"; features {', '.join(feats)}")
            for k, p in r.props.items():
                if p["string"]:
                    lines.append(f"        {k} = {p['string']}")
    return lines


def _cbvm():
    try:
        from hdkit.fx import cbvm
        return cbvm
    except ImportError:
        return None


def survey_file(pf: PFile, cb=None) -> dict:
    """What one effect needs: classes, renderers (kind|material, enabled features), CBVM ops, engine calls."""
    eff = graph.load(b"", pf=pf)
    classes = collections.Counter(o.cls for o in pf.objects)
    rk = collections.Counter(f"{r.kind}|{r.material}" for r in eff.renderers)
    feats = collections.Counter(k for r in eff.renderers for k, p in r.props.items()
                                if p["type"] == "feature" and p["value"])
    calls, ops = collections.Counter(), collections.Counter()
    for s in eff.scripts.values():
        calls.update(c for c in s.calls if c)
        if cb is not None:
            prog = pf.obj(s.obj).get(2)
            h1 = int.from_bytes(prog[4:8], "little")
            for ins in cb.decode(prog[36:36 + h1]):
                nm = cb.OPS[ins.op]
                if ins.op in (0x70, 0x79):
                    nm += f".{ins.imm[0]}"
                elif ins.op in (0x77, 0x78):
                    nm += "." + (cb.MATH[ins.imm[0]] if ins.imm[0] < len(cb.MATH) else str(ins.imm[0]))
                ops[nm] += 1
    return {"layers": len(eff.layers), "renderers": dict(rk), "features": dict(feats), "classes": dict(classes),
            "scripts": len(eff.scripts), "ops": dict(ops), "calls": dict(calls),
            "samplers": dict(collections.Counter(s.kind for s in eff.samplers))}


def format_main(argv=None) -> int:
    """The format lane's commands: info, dump, survey, roundtrip."""
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx", description="info, dump, survey, roundtrip")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("info")
    a.add_argument("file")
    a.add_argument("--platform", default="PC")
    a = sub.add_parser("dump")
    a.add_argument("file")
    a.add_argument("-o", "--out")
    a.add_argument("--platform", default="PC")
    a = sub.add_parser("survey")
    a.add_argument("paths", nargs="+")
    a.add_argument("-o", "--out")
    a = sub.add_parser("roundtrip")
    a.add_argument("paths", nargs="+")
    a.add_argument("-o", "--out", help="write the per-file results as JSON")
    args = ap.parse_args(argv)

    if args.cmd == "info":
        print("\n".join(info(graph.load(args.file, platform=args.platform))))
    elif args.cmd == "dump":
        text = json.dumps(graph.load(args.file, platform=args.platform).to_json(), indent=1)
        if args.out:
            Path(args.out).write_text(text)
        else:
            print(text)
    elif args.cmd == "survey":
        cb, per, tot = _cbvm(), {}, collections.defaultdict(collections.Counter)
        for root, f in files(args.paths):
            try:
                s = survey_file(PFile.read(f.read_bytes()), cb)
            except Exception as e:  # noqa: BLE001 (a survey reports bad files, it does not stop)
                s = {"error": f"{type(e).__name__}: {e}"}
            per[str(f.relative_to(root))] = s
            for k in ("renderers", "features", "classes", "ops", "calls", "samplers"):
                tot[k].update(s.get(k, {}))
        out = {"files": len(per), "errors": sum("error" in s for s in per.values()),
               "totals": {k: dict(v.most_common()) for k, v in tot.items()}, "per_file": per}
        if args.out:
            Path(args.out).write_text(json.dumps(out, indent=1))
        print(f"{out['files']} files, {out['errors']} errors; renderers {sum(tot['renderers'].values())}, "
              f"scripts ops {sum(tot['ops'].values())}, distinct calls {len(tot['calls'])}")
    elif args.cmd == "roundtrip":
        t0, n, same, bad, res = time.time(), 0, 0, 0, {}
        objs = 0
        for root, f in files(args.paths):
            b = f.read_bytes()
            n += 1
            try:
                pf = PFile.read(b)
                ok = pf.data() == b
                probs = pf.check()
                eff = graph.load(b"", pf=pf)
                objs += len(pf.objects)
                same += ok
                r = {"identical": ok, "problems": probs[:5], "layers": len(eff.layers)}
            except Exception as e:  # noqa: BLE001
                r = {"identical": False, "error": f"{type(e).__name__}: {e}"}
            if not r["identical"] or r.get("problems"):
                bad += 1
                print(f"{f}: {r}")
            res[str(f.relative_to(root))] = r
        print(f"{n} files, {objs} objects: {same} byte-exact, {bad} with problems ({time.time() - t0:.1f} s)")
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1))
        return 0 if bad == 0 else 1
    return 0


def _context(argv):
    from . import context
    return context.main(argv)


def _coverage(argv):
    from . import coverage
    return coverage.main(argv)


def _format(cmd):
    return lambda argv: format_main([cmd] + list(argv))


def _vm(cmd):
    def run(argv):
        from . import cli
        return cli.main([cmd] + list(argv))
    return run


COMMANDS = {"context": _context, "coverage": _coverage,
            **{c: _format(c) for c in ("info", "dump", "survey", "roundtrip")},
            **{c: _vm(c) for c in ("sim", "vmcov", "dis")},      # vm lane: hdkit.fx.cli
            "langvm": lambda argv: __import__("hdkit.fx.vmcheck", fromlist=["main"]).main(list(argv))}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("usage: python -m hdkit.fx {%s} ..." % ",".join(sorted(COMMANDS)))
        return 2
    return COMMANDS[argv[0]](argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
