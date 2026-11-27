"""The game's camera and a unit's placement, read from a RenderDoc extraction (rd_extract.py: draws/<eid>/), so a viewer
render can use the capture's own numbers instead of fitted estimates.

cb3 (view constants, calib r3 FINDINGS.md): row 0 (W, H, 1/W, 1/H); rows 5..8 the view matrix and rows 13..16 the
orthographic projection, both row-vector (p_clip = p_world @ V @ P); row 16 .xy carries the TAA jitter (a fraction of a
pixel, dropped unless asked for). A model draw's world matrix is the first 16 floats its push word 0 points to (row-vector,
translation in row 3); a particle mesh draw's per-instance matrices are in decoded.json `streams.matrix`.

The viewer and the game share their world axes (camera.basis(45, 30) equals the capture's right/up/towards-camera rows),
so positions and yaws carry over directly: yaw is camera.rot_y's angle (positive turns +z towards +x).

    gv = from_draws(cam_draw_dir, unit_draw_dir)   # GameView: el, az, k (px/unit), size, feet pixel, unit yaw/scale/lift
    gv.as_dict() -> JSON for a side-by-side runner's --game-view
"""
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


def _rows(path: Path) -> np.ndarray:
    b = Path(path).read_bytes()
    n = len(b) // 16 * 16
    return np.frombuffer(b[:n], "<f4").reshape(-1, 4).astype(float)


def find_cb3(draw_dir) -> np.ndarray | None:
    """The cb3 rows of a draw: the push-pointed block whose row 0 is (W, H, 1/W, 1/H) and whose rows 13..16 are an
    orthographic projection (w column 0, 0, 0, 1)."""
    dd = Path(draw_dir)
    info = json.loads((dd / "draw.json").read_text(encoding="utf-8"))
    for p in info.get("push_pointers", []):
        f = dd / p.get("file", "")
        if not f.is_file():
            continue
        r = _rows(f)
        if len(r) < 20:
            continue
        w, h, iw, ih = r[0]
        if w > 0 and h > 0 and abs(iw * w - 1) < 1e-2 and abs(ih * h - 1) < 1e-2 and abs(r[16, 3] - 1) < 1e-6 \
                and np.allclose(r[13:16, 3], 0.0) and abs(r[8, 3] - 1) < 1e-6:
            return r
    return None


def world_matrix(draw_dir, push_offset: int = 0) -> np.ndarray:
    """A model draw's world matrix (4x4, row-vector): the first 16 floats its push word `push_offset` points to."""
    dd = Path(draw_dir)
    info = json.loads((dd / "draw.json").read_text(encoding="utf-8"))
    for p in info.get("push_pointers", []):
        if int(p.get("push_offset", -1)) == push_offset:
            return _rows(dd / p["file"])[:4].copy()
    raise FileNotFoundError("%s: no push pointer at %d" % (dd, push_offset))


def placement(M) -> dict:
    """Position, yaw (degrees, camera.rot_y convention), per-axis scale of a row-vector world matrix."""
    M = np.asarray(M, float)
    sc = np.linalg.norm(M[:3, :3], axis=1)
    R = (M[:3, :3] / sc[:, None]).T                 # column-vector rotation
    yaw = math.degrees(math.atan2(R[0, 2], R[0, 0]))
    return {"position": [float(v) for v in M[3, :3]], "yaw": round(yaw, 4), "scale": [round(float(v), 5) for v in sc]}


@dataclass
class GameView:
    size: tuple                     # capture (W, H)
    el: float
    az: float
    k: float                        # px per world unit (horizontal; vertical checked equal)
    eye: list                       # camera position (cb3 row 12)
    feet: tuple | None = None       # unit origin's pixel in the capture
    unit: dict = field(default_factory=dict)   # placement() of the unit's world matrix
    V: list = field(default_factory=list)
    P: list = field(default_factory=list)

    def project(self, world, jitter: bool = False) -> tuple:
        V, P = np.asarray(self.V), np.asarray(self.P).copy()
        if not jitter:
            P[3, :2] = 0.0
        c = np.r_[np.asarray(world, float), 1.0] @ V @ P
        W, H = self.size
        return float((c[0] + 1) / 2 * W), float((1 - c[1]) / 2 * H)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["size"] = list(self.size)
        d["feet"] = list(self.feet) if self.feet else None
        return d

    @classmethod
    def from_dict(cls, d) -> "GameView":
        d = dict(d)
        d["size"] = tuple(d["size"])
        d["feet"] = tuple(d["feet"]) if d.get("feet") else None
        return cls(**d)


def from_cb3(r, size=None) -> GameView:
    r = np.asarray(r, float)
    W, H = (int(r[0, 0]), int(r[0, 1])) if size is None else size
    V = np.vstack([r[5:8], r[8]])
    P = r[13:17].copy()
    right, up, towards = V[:3, 0], V[:3, 1], V[:3, 2]
    el = math.degrees(math.asin(max(-1.0, min(1.0, towards[1]))))
    az = math.degrees(math.atan2(towards[0], towards[2]))
    from ...view import camera
    r2, u2, w2 = camera.basis(az, el)
    if not (np.allclose(r2, right, atol=1e-3) and np.allclose(u2, up, atol=1e-3) and np.allclose(w2, towards, atol=1e-3)):
        raise ValueError("capture view basis does not match camera.basis(%.3f, %.3f)" % (az, el))
    kx, ky = P[0, 0] * W / 2.0, P[1, 1] * H / 2.0
    if abs(kx - ky) > 1e-2 * kx:
        raise ValueError("non-square pixels: %g vs %g px/unit" % (kx, ky))
    return GameView((W, H), round(el, 4), round(az, 4), float(kx), [float(v) for v in r[12, :3]],
                    V=V.tolist(), P=P.tolist())


def from_draws(cam_draw, unit_draw=None, unit_matrix=None) -> GameView:
    """GameView from a draw that binds cb3 (any particle or model draw) and, optionally, a unit's model draw (or its
    world matrix): feet = the projected origin of the unit's world matrix."""
    r = find_cb3(cam_draw)
    if r is None:
        raise ValueError("%s: no cb3 block found" % cam_draw)
    gv = from_cb3(r)
    M = unit_matrix if unit_matrix is not None else (world_matrix(unit_draw) if unit_draw else None)
    if M is not None:
        gv.unit = placement(M)
        gv.feet = gv.project(gv.unit["position"])
    return gv


# ---- the unit's skeleton as the game poses it ------------------------------------------------------------------------------

def palette(draw_dir, push_offset: int = 16) -> np.ndarray:
    """A skinned model draw's bone palette: (n, 3, 4) rows (skin matrix = bone world x inverse bind, row-major 3x4; the
    D2R models VS reads it through push word 16, three float4 per bone, indexed by the vertex's joint byte, in the order
    of the mesh's bone bindings)."""
    dd = Path(draw_dir)
    info = json.loads((dd / "draw.json").read_text(encoding="utf-8"))
    for p in info.get("push_pointers", []):
        if int(p.get("push_offset", -1)) == push_offset:
            r = _rows(dd / p["file"])
            return r[:len(r) // 3 * 3].reshape(-1, 3, 4)
    raise FileNotFoundError("%s: no push pointer at %d" % (dd, push_offset))


def root_offsets(pal, skeleton, bone_names, clip=None, times=None, pose_kw=None) -> dict:
    """Per skeleton root: the translation the game adds to every bone of that root's tree, measured as the middle value (p50) of
    (game skin translation - viewer skin translation) over the bones bound by a mesh, at the clip time that matches the
    palette best (rotations are compared to pick it). Returns {"time", "rot_err", "roots": {root: {"offset", "spread",
    "bones"}}}. Mephisto (calib r3): spine_01 tree +6.906 y, pelvis tree +0.777 y (spread < 0.2)."""
    from ...view import gr2
    pal = np.asarray(pal, float)
    ix = [skeleton.idx[n] for n in bone_names]
    G = pal[:len(ix)]
    best = None
    for t in (times if times is not None else ([0.0] if clip is None else np.arange(0.0, clip.duration, 0.01))):
        pose = gr2.pose_at(skeleton, clip, float(t), **(pose_kw or {"loop": True}))
        S = (np.asarray(pose.worlds) @ skeleton.iwt)[ix, :3, :]
        e = float(np.abs(S[:, :, :3] - G[:, :, :3]).mean())
        if best is None or e < best[0]:
            best = (e, float(t), S)
    e, t, S = best

    def root(i):
        while skeleton.parent[i] >= 0:
            i = skeleton.parent[i]
        return skeleton.names[i]
    groups = {}
    for k, i in enumerate(ix):
        groups.setdefault(root(i), []).append(G[k, :, 3] - S[k, :, 3])
    out = {}
    for r, v in groups.items():
        v = np.array(v)
        out[r] = {"offset": [round(float(x), 4) for x in np.percentile(v, 50, axis=0)],
                  "spread": round(float(np.percentile(v[:, 1], 90) - np.percentile(v[:, 1], 10)), 4), "bones": len(v)}
    return {"time": round(t, 3), "rot_err": round(e, 5), "roots": out}


def offset_pose(pose, skeleton, offsets: dict):
    """A gr2.Pose with each root tree moved by its offset {root name: [x, y, z]} (model space, before the unit's yaw)."""
    W = np.array(pose.worlds, float)
    for i in range(len(skeleton.names)):
        j = i
        while skeleton.parent[j] >= 0:
            j = skeleton.parent[j]
        o = offsets.get(skeleton.names[j])
        if o is not None:
            W[i, :3, 3] += np.asarray(o, float)
    pose.worlds = W
    return pose
