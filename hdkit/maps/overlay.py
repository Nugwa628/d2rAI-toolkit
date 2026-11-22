"""A map's HD build as a mod data overlay: the preset of each map and every file it needs, copied out of a build.

From a build folder (a mod data folder with the maps' HD presets and their art, e.g. a 4x upscaled build) it copies each
map's preset (data/hd/env/preset/<map>.json) and everything it references there, followed through the files: models
(with their _lodN siblings), textures, lights, particles. It also copies the build's data/hd/model_lod_desc.bin (the base
mod's table plus the build's models). The result goes into OUT/data, the same layout as the mod's own data folder, so it
can be laid over an installed mod for a test run (the testing tools' pre-launch step) or packed with a release. A reference
that the build does not hold is the base mod's own (biomes, stock models): it is listed in OUT/overlay.json, not copied.
Files are copied byte for byte (sha256 in OUT/overlay.json).

  python -m hdkit.maps overlay BUILD_DATA OUT MAP [MAP ...]     # MAP: the path below data/global/tiles, no .ds1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REF = re.compile(rb"data/hd/[A-Za-z0-9_/\-.]+?\.(?:model|texture|json|light|particles)")


def closure(build: Path, maps) -> tuple:
    """(files, outside): the build's files the maps need (paths relative to build's parent, "data/hd/..."), and the
    referenced paths the build does not hold."""
    root = build.parent
    todo = [root / ("data/hd/env/preset/%s.json" % m) for m in maps]
    for f in todo:
        if not f.is_file():
            raise FileNotFoundError("no preset %s" % f)
    files, outside, seen = set(), set(), set()
    while todo:
        f = todo.pop()
        rel = f.relative_to(root).as_posix()
        if rel in files:
            continue
        files.add(rel)
        for m in set(REF.findall(f.read_bytes())):
            r = m.decode()
            if r in seen:
                continue
            seen.add(r)
            p = root / r
            cands = [p] if p.is_file() else []
            if r.endswith(".model") and p.parent.is_dir():
                cands += sorted(p.parent.glob(p.stem + "_lod*.model"))
            if not cands:
                outside.add(r)
            todo += cands
    return files, outside


def build_overlay(build: Path, out: Path, maps) -> dict:
    build = build.resolve()
    if build.name != "data":
        raise ValueError("BUILD_DATA must be a mod data folder (.../data)")
    files, outside = closure(build, maps)
    lod = build / "hd" / "model_lod_desc.bin"
    if lod.is_file():
        files.add("data/hd/model_lod_desc.bin")
    done = []
    for rel in sorted(files):
        src, dst = build.parent / rel, out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        done.append({"path": rel, "bytes": dst.stat().st_size, "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()})
    rep = {"build": str(build), "maps": list(maps), "files": done, "bytes": sum(d["bytes"] for d in done),
           "base_mod_refs": sorted(outside)}
    (out / "overlay.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.maps overlay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("maps", nargs="+")
    a = ap.parse_args(argv)
    rep = build_overlay(a.build, a.out, [m[:-4] if m.endswith(".ds1") else m for m in a.maps])
    print("%d maps: %d files, %.1f MB into %s; %d references left to the base mod" % (
        len(rep["maps"]), len(rep["files"]), rep["bytes"] / 1e6, a.out / "data", len(rep["base_mod_refs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
