"""LODs: the UV-mapped mesh (body, and a rigid weapon when the folder holds one) and its five LODs (Blender Decimate,
UVs kept).

  python -m hdkit.monster3d.s4_lods [--monster <monster.json>] <final dir (mesh_uv.npz[, weapon.npz])> <out dir>
        [--ratios 0.5,0.25,0.12,0.06]

Writes <out>/mesh_lods.npz (pos, nrm, uv, tris and the _1 .. _4 suffixes; upright body frame, height 1). A weapon is told
from the body by its uv (the bottom quarter of the atlas), so it survives the decimation as a piece of its own.
Blender (common.BLENDER, a separate process) runs hdkit/monster3d/blender/unwrap_bl.py; hdkit.sprite3d.obj_npz converts.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

from hdkit.monster3d import common
from hdkit.sprite3d import render_soft


def main(a):
    src, out = Path(a[0]), Path(a[1])
    ratios = a[a.index("--ratios") + 1] if "--ratios" in a else "0.5,0.25,0.12,0.06"
    out.mkdir(parents=True, exist_ok=True)
    m = np.load(src / "mesh_uv.npz")
    pos, tris, uv, nrm = m["pos"], m["tris"], m["uv"], m["nrm"]
    has_weapon = (src / "weapon.npz").is_file()
    if has_weapon:
        ax = np.load(src / "weapon.npz")
        tris = np.vstack([tris, ax["tris"] + len(pos)])
        pos, uv = np.vstack([pos, ax["pos"]]), np.vstack([uv, ax["uv"]])
        nrm = np.vstack([nrm, render_soft.vertex_normals(ax["pos"], ax["tris"])])
    np.savez(out / "lod0.npz", pos=pos, nrm=nrm, uv=uv, tris=tris)
    py = [sys.executable, "-m", "hdkit.sprite3d.obj_npz"]
    env = common.child_env()
    subprocess.run([*py, "to_obj", str(out / "lod0.npz"), str(out / "in.obj"), "--uv"], check=True, env=env)
    logs = out / "logs"
    logs.mkdir(exist_ok=True)
    bl = common.PKG / "blender" / "unwrap_bl.py"
    with open(logs / "blender-lods.log", "w") as log:
        subprocess.run([str(common.BLENDER), "-b", "--factory-startup", "-P", str(bl), "--", str(out / "in.obj"),
                        str(out / "set"), "--keep-uv", "--ratios", ratios], check=True, stdout=log, stderr=subprocess.STDOUT)
    subprocess.run([*py, "to_npz", str(out / "mesh_lods.npz")] + [str(out / ("set_lod%d.obj" % k)) for k in range(5)],
                   check=True, env=env)
    d = np.load(out / "mesh_lods.npz")
    for k in range(5):
        s = "" if k == 0 else "_%d" % k
        wp = (d["uv" + s][:, 1] > 0.75) & has_weapon
        print("lod %d: %d vertices, %d triangles (weapon: %d vertices, %d triangles)"
              % (k, len(d["pos" + s]), len(d["tris" + s]), int(wp.sum()), int(wp[d["tris" + s]].all(1).sum())))


if __name__ == "__main__":
    main(sys.argv[1:])
