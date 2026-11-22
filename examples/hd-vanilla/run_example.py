#!/usr/bin/env python3
"""The hdkit example on vanilla files only: the viewer on one vanilla monster, the map viewer on one vanilla map.

  python examples/hd-vanilla/run_example.py [--root base=<folder with data/hd/...>] [--monster mummy1]
                                            [--map act1/cathedrl/cathy3] [--dry]

Run it with a python that has hdkit/requirements.txt, from the repository root, with HDKIT_CONFIG pointing at your
hdkit.toml (examples/hd-vanilla/hdkit.toml is a template). Without --root the base game comes from CASC through the
capped cache ([game] dir in the config). Every output goes to the config's work folder; nothing is written anywhere else,
and no game is started. GPU steps wait for the GPU gate and stop if it stays busy.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def steps(a) -> list:
    roots = ["--root", a.root] if a.root else []
    unit = "hd/character/enemy/%s.json" % a.monster
    out = Path(a.out) if a.out else None
    o = (lambda name: ["--out", str(out / name)]) if out else (lambda name: [])
    return [
        ("may the GPU run now", ["gate"]),
        ("the game camera", ["camera"]),
        ("the unit and its clips", ["ls", "clips", unit] + roots),
        ("one frame, walking, facing key 2", ["render", unit, "--mode", "wl", "--facing", "2"] + roots + o("render")),
        ("sheet: neutral, walk, attack x 8 facings", ["sheet", unit, "--modes", "nu,wl,a1", "--facings", "8"] + roots
         + o("sheet")),
        ("turntable", ["turntable", unit, "--frames", "16"] + roots + o("turntable")),
        ("the map's facts", ["map", "info", a.map, "--source", "d2r"]),
        ("the map in classic graphics", ["map", "classic", a.map, "--source", "d2r"] + o("map")),
        ("classic | HD side by side (HD needs the base game's preset: CASC or a root holding hd/env/preset)",
         ["map", "sheet", a.map, "--source", "d2r"] + roots + o("map")),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="name=folder holding data/hd/... (default: casc)")
    ap.add_argument("--monster", default="mummy1", help="a vanilla HD unit name (default mummy1)")
    ap.add_argument("--map", default="act1/cathedrl/cathy3", help="a vanilla map below data/global/tiles")
    ap.add_argument("--out", default=None, help="output folder (default: the tools' own, under the config's work)")
    ap.add_argument("--dry", action="store_true", help="print the commands only")
    a = ap.parse_args(argv)
    failed = 0
    for what, args in steps(a):
        cmd = [sys.executable, "-m", "hdkit.view"] + args
        print("==", what, "\n  ", " ".join(cmd), flush=True)
        if a.dry:
            continue
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode:
            print("   -> exit %d" % r.returncode, flush=True)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
