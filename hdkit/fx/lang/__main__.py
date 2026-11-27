"""hdfx-lang command line (docs/hdfx-lang.md):

  python -m hdkit.fx.lang decompile <file.particles> [--obj N] [-o out.hfx]     readable source of one/every script
  python -m hdkit.fx.lang compile <src.hfx> [--into <file.particles> --obj N -o <out.particles>] [--listing]
        compile; with --into, replace object N's script (both evolve copies: pass --obj twice) and write a new file
  python -m hdkit.fx.lang check <file.particles|folder> ...    decompile -> compile -> same behaviour, per script
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import LangError, check, compile, decompile
from .isa import cb


def _scripts(path: Path):
    from hdkit.formats import hd_particles as hp
    return cb.scripts(hp.ParticleFile(path.read_bytes()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx.lang", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("decompile")
    a.add_argument("file")
    a.add_argument("--obj", type=int, help="object number (1-based, as hd_cbvm lists it)")
    a.add_argument("-o", "--out")
    a = sub.add_parser("compile")
    a.add_argument("src")
    a.add_argument("--into", help="a .particles file whose script(s) the source replaces")
    a.add_argument("--obj", type=int, action="append", help="object number(s) to replace (1-based)")
    a.add_argument("-o", "--out")
    a.add_argument("--listing", action="store_true", help="print the CBVM listing")
    a = sub.add_parser("check")
    a.add_argument("paths", nargs="+")
    a.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "decompile":
        out = []
        for s in _scripts(Path(args.file)):
            if args.obj is None or s.obj + 1 == args.obj:
                try:
                    out.append(decompile(s))
                except LangError as e:
                    out.append(f"// object {s.obj + 1}: cannot decompile: {e}\n")
        text = "\n".join(out)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    if args.cmd == "compile":
        src = Path(args.src).read_text(encoding="utf-8")
        try:
            if args.into:
                from hdkit.formats import hd_particles as hp
                data = Path(args.into).read_bytes()
                pf = hp.ParticleFile(data)
                old = {s.obj + 1: s for s in cb.scripts(pf)}
                new = []
                for n in args.obj or []:
                    if n not in old:
                        print(f"object {n} is not a script", file=sys.stderr)
                        return 2
                    s = compile(src, base=old[n])
                    s.obj = n - 1
                    new.append(s)
                if not new or not args.out:
                    print("--into needs --obj and -o", file=sys.stderr)
                    return 2
                Path(args.out).write_bytes(cb.write_scripts(pf, new))
                print(f"{len(new)} script(s) replaced -> {args.out}")
            else:
                s = compile(src)
        except LangError as e:
            print(f"{args.src}: {e}", file=sys.stderr)
            return 1
        if args.listing:
            for s in (new if args.into else [s]):
                sys.stdout.write(cb.script_text(s))
        elif not args.into:
            print(f"ok: {len(s.code)} instructions, {len(s.consts)} constants, {len(s.ext_names)} externals")
        return 0

    files = [f for p in map(Path, args.paths) for f in (sorted(p.rglob("*.particles")) if p.is_dir() else [p])]
    total = bad = 0
    for f in files:
        if not f.stat().st_size:
            continue
        for s in _scripts(f):
            total += 1
            r = check(s)
            if r:
                bad += 1
                if args.verbose or bad <= 20:
                    print(f"{f} object {s.obj + 1}: {r[0]}")
    print(f"{len(files)} files, {total} scripts, {bad} do not come back")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
