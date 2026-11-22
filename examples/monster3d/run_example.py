#!/usr/bin/env python3
"""The sprite-to-3D example on vanilla files only: a designed body on D2R's own Dried Corpse, judged against its classic frames.

  python examples/monster3d/run_example.py [--work <dir>] [--blender] [--dry]

Run it from the repository root with a python that has hdkit/requirements.txt (numpy, scipy, Pillow, wgpu, scikit-image,
numba). It needs your own extraction of the game's files, named in hdkit.toml or by the environment (the toolkit stores
none):
  HDKIT_M3D_D2R       D2R's data folder holding hd/character/enemy/mummy1/... (models, skeleton, clips, textures)
                      (default: hdkit.toml [mod] d2r_extract + /data)
  HDKIT_CLASSIC       a folder holding data/global/monsters/mm and data/global/excel (D2R's own classic files)
  HDKIT_PALETTE_ROOT  a folder holding data/global/palette/act1/pal.dat (or [game] dir for a CASC read)
Every output goes to --work (default <hdkit work>/monster3d-example). Nothing is written anywhere else and no game is
started. The GPU steps (viewer sheets) and the Blender bake wait for nothing: run them when the machine is free
(`python -m hdkit.gpugate`).

Steps: classic frames -> a base stage from the donor -> a procedural body -> its viewer sheet; with --blender also the
designed body of the guide (design graph, Blender model, displacement and paint, Cycles bake, weights, build) with the
worked example's design, shape and paint inputs (examples/monster3d/fungus/*.json, numbers only).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def steps(a, work: Path) -> list:
    mon = ["--monster", str(HERE / "vanilla.json")]
    m3d = [sys.executable, "-m", "hdkit.monster3d"]
    frames = work / "example_mummy" / "stages" / "1-frames" / "classic"
    base, proc, hd, body = work / "st-base", work / "st-procbody", work / "hd", work / "st-hdbody"
    ex = HERE / "fungus"
    blender = os.environ.get("HDKIT_M3D_BLENDER") or "blender"
    bl = [blender, "-b", "--factory-startup", "-noaudio", "--python"]
    out = [
        ("classic frames of every mode x 8 facings", m3d + ["s1_frames"] + mon + ["classic"]),
        ("a base stage from the donor alone", m3d + ["s0_base"] + mon + [str(base)]),
        ("a procedural body on the edited skeleton, clips retargeted", m3d + ["s4_procbody", "build", str(base), str(proc)] + mon),
        ("skin stretch per clip", m3d + ["s5_stretch"] + mon + [str(proc / "overlay")]),
        ("review sheet: classic | base | procedural body (GPU)",
         m3d + ["g7_viewersheet"] + mon + [str(frames), str(work / "sheet-procbody.png"), "--stage", "base=%s" % (base / "overlay"),
                                           "--stage", "procbody=%s" % (proc / "overlay"), "--modes", "nu:0,wl:5,a1:7"]),
    ]
    if a.blender:
        out += [
            ("design graph on the edited skeleton", m3d + ["s4_hdbody", "design", str(base), str(hd), "--params", str(ex / "params.json"),
                                                          "--design", str(ex / "design.json")] + mon),
            ("Blender: skin modifier + subdivision", bl + [str(REPO / "hdkit/monster3d/blender/hdbody_bl.py"), "--", "model", str(hd)]),
            ("displacement and paint", m3d + ["s4_hdbody", "shape", str(hd), "--shape", str(ex / "shape.json"), "--paint",
                                              str(ex / "paint.json")] + mon),
            ("Blender: UVs in free texels, Cycles bakes, LODs (tens of minutes)",
             bl + [str(REPO / "hdkit/monster3d/blender/hdbody_bl.py"), "--", "bake", str(hd), "--ao-samples", "16"]),
            ("weights from the design graph", m3d + ["s4_hdbody", "weights", str(hd), "--smooth", "30", "--min-w", "0.02"] + mon),
            ("the stage with the designed body", m3d + ["s4_hdbody", "build", str(base), str(body), str(hd)] + mon),
            ("review sheet: classic | procedural | designed (GPU)",
             m3d + ["g7_viewersheet"] + mon + [str(frames), str(work / "sheet-hdbody.png"), "--stage", "procbody=%s" % (proc / "overlay"),
                                               "--stage", "hdbody=%s" % (body / "overlay"), "--modes", "nu:0,wl:5,a1:7"]),
        ]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=None, help="output folder (default <hdkit work>/monster3d-example)")
    ap.add_argument("--blender", action="store_true", help="also the designed body (needs Blender 4.5)")
    ap.add_argument("--dry", action="store_true", help="print the commands only")
    a = ap.parse_args(argv)
    if a.work:
        work = Path(a.work).resolve()
    else:
        sys.path.insert(0, str(REPO))
        from hdkit import site
        work = Path(site.work_root()) / "monster3d-example"
    env = dict(os.environ, HDKIT_M3D_DIR=str(work))
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    for what, cmd in steps(a, work):
        print("==", what, "\n  ", " ".join(cmd), flush=True)
        if a.dry:
            continue
        r = subprocess.run(cmd, cwd=REPO, env=env)
        if r.returncode:
            print("   -> exit %d: stopped (each later step needs this one)" % r.returncode, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
