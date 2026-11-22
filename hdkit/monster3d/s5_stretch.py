"""Skinning check of a stage: how far the posed mesh's edges stretch from the bind pose.

  python -m hdkit.monster3d.s5_stretch [--monster <monster.json>] <stage overlay dir> [--clips neutral,walk,...] [--step 0.05]
        [--json <out>] [--part-bone BONE] [--folder hd/character/enemy/...]

Every clip of the stage's combined.animations is sampled every --step seconds with hdkit.view's own pose and skin code
(what the viewer and the web bundle draw), on LOD0. Per sample: each triangle edge's posed length over its bind length.
A limb smear (one leg pulled into a long streak) shows as edges stretched far beyond 1. Reported
per clip, for the sample with the most excess length (sum over body edges of max(0, posed - 1.5 x bind), model units:
many tiny edges stretched a little count little, a long smear counts a lot): that excess, the p99.9 and max ratio, and
the share of body edges above 1.5 and 2.0 (the part bone's
vertices, the rigid part, are left out: they are rigid). The unit folder and the part bone come from the monster config
(folder, part_bone) unless --folder / --part-bone are given; without either, the first folder under the overlay that holds a
torso_lod0.model is used.
"""
import json
import sys
from pathlib import Path

import numpy as np

from hdkit.monster3d import common
from hdkit.view import gr2


def unit_dir(overlay, folder=None) -> Path:
    folder = folder or (common.FOLDER if common.MONSTER["name"] else "")
    if folder:
        return Path(overlay) / folder
    return next(Path(overlay).rglob("torso_lod0.model")).parent


def load(overlay, folder=None):
    d = unit_dir(overlay, folder)
    meshes, sk = gr2.load_meshes((d / "torso_lod0.model").read_bytes())
    clips = gr2.load_clips((d / "animation" / "combined.animations").read_bytes(), sk)
    return meshes, sk, clips


def edges(tris):
    e = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e.sort(1)
    return np.unique(e, axis=0)


def edge_stretch(bind, posed, tris, body=None, limit=1.5):
    """Per edge between two `body` vertices: posed length / bind length (edges longer than 1e-5 in the bind pose), and
    the excess max(0, posed - limit x bind)."""
    E = edges(np.asarray(tris))
    if body is not None:
        E = E[body[E[:, 0]] & body[E[:, 1]]]
    lb = np.linalg.norm(bind[E[:, 0]] - bind[E[:, 1]], axis=1)
    lp = np.linalg.norm(posed[E[:, 0]] - posed[E[:, 1]], axis=1)
    ok = lb > 1e-5
    return lp[ok] / lb[ok], np.maximum(lp - limit * lb, 0)


def check(overlay, names=None, step=0.05, part_bone="", folder=None):
    meshes, sk, clips = load(overlay, folder)
    return check_loaded(meshes, sk, clips, names, step, part_bone)


def check_loaded(meshes, sk, clips, names=None, step=0.05, part_bone=""):
    out = {}
    for name, clip in clips.items():
        if names and name not in names:
            continue
        worst = None
        for t in np.arange(0, clip.duration + 1e-6, step):
            pose = gr2.pose_at(sk, clip, float(t))
            ratios, excess = [], []
            for m in meshes:
                if m.joints is None:
                    continue
                pb = m.bones.index(part_bone) if part_bone in m.bones else -1
                body = ~((m.joints[:, 0] == pb) & (m.weights[:, 0] > 0.99)) if pb >= 0 else np.ones(len(m.pos), bool)
                P, _n, _t = gr2.skin(m, sk, pose)
                r_, ex_ = edge_stretch(m.pos, P, m.tris, body)
                ratios.append(r_)
                excess.append(ex_)
            r = np.concatenate(ratios)
            ex = float(np.concatenate(excess).sum())
            row = {"excess": round(ex, 2), "t": round(float(t), 3), "p999": round(float(np.percentile(r, 99.9)), 3), "max": round(float(r.max()), 2),
                   "over1_5": round(float((r > 1.5).mean()), 5), "over2": round(float((r > 2.0).mean()), 5)}
            if worst is None or row["excess"] > worst["excess"]:
                worst = row
        out[name] = {"duration": round(clip.duration, 3), "worst": worst}
    return out


def main():
    a = sys.argv[1:]
    opt = lambda n, d: a[a.index(n) + 1] if n in a else d  # noqa: E731
    names = opt("--clips", None)
    res = check(a[0], names.split(",") if names else None, float(opt("--step", "0.05")),
                opt("--part-bone", common.MONSTER.get("part_bone") or ""), opt("--folder", None))
    print(json.dumps(res, indent=1))
    if "--json" in a:
        Path(opt("--json", "")).write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
