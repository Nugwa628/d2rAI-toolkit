"""Models, skeletons and clips of D2R's Granny files, posed and skinned on the CPU.

Reused, not copied: hdkit/formats/d2r_model.py (the Granny tree reader) and hdkit/formats/hd_gltf.py (`decode_mesh`, `_maps`: vertices,
triangles, skin and material texture paths of a mesh), both on main.

New here: the pose maths and the curve sampler. A bone's local transform is T(position) R(orientation) SS(scale/shear); its
world transform is the parent's world times its local; a vertex is skinned by sum_k w_k W[j_k] IWT[j_k] p, where IWT is the
bone's InverseWorldTransform (stored transposed, as the death-clip step (docs/monster-sprite-to-3d.md step 9) reads it). Quaternions are
(x, y, z, w).

Curves: every Granny curve kind is decoded and sampled by `curves.py` (decoders ported from LSLib, exact B-spline
sampling of our own). `pose_at(..., method="keys")` gives the LSLib / Level Editor reading for comparison.
Pose rules checked against the Reimagined Level Editor's `CharacterAnimation.cs` (MIT, @3f76c75): a part a track does not
animate keeps the bone's rest value, a mesh's bone slots resolve through its own BoneBindings names.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..formats import d2r_model as dm
from ..formats import hd_gltf
from . import curves

SUPPORTED = curves.KINDS
UnsupportedCurve = curves.UnsupportedCurve


def load_tree(data: bytes):
    g = dm.Gr2File.read(data)
    return g, dm.parse_tree(g)


def qmat(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def compose(pos, quat, ss) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = qmat(np.asarray(quat, float) / max(np.linalg.norm(quat), 1e-12)) @ np.asarray(ss, float).reshape(3, 3)
    M[:3, 3] = pos
    return M


# ---- curves ------------------------------------------------------------------------------------------------------------------

def curve_kind(curve) -> str:
    """'DaK32fC32f' etc. of a curve (the CurveData variant of a track's Orientation/Position/ScaleShearCurve)."""
    v = curve["CurveData"] if isinstance(curve, dm.Obj) and "CurveData" in curve.f else curve
    return list(v.obj.f)[0].replace("CurveDataHeader_", "")


def sample_curve(curve, t: float, dim: int, method: str = "bspline", loop: bool = False, duration: float | None = None):
    """The curve's value at time t (dim floats), or None for an identity curve. Raises UnsupportedCurve."""
    c = curve if isinstance(curve, curves.Curve) else curves.decode(curve, dim)
    v = curves.sample(c, t, method=method, loop=loop, duration=duration)
    return None if v is None else v[:dim]


# ---- skeleton and pose ----------------------------------------------------------------------------------------------------------

class Skeleton:
    def __init__(self, sk):
        bones = sk["Bones"]
        self.name = sk["Name"]
        self.names = [b["Name"] for b in bones]
        self.parent = [int(b["ParentIndex"]) for b in bones]
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.rest = []          # (pos, quat, ss) per bone
        for b in bones:
            tr = b["Transform"]
            self.rest.append((np.array(tr.position, float), np.array(tr.orientation, float), np.array(tr.scale_shear, float)))
        self.iwt = np.stack([np.array(b["InverseWorldTransform"], float).reshape(4, 4).T for b in bones])

    def worlds(self, locals_) -> np.ndarray:
        W = np.zeros((len(self.names), 4, 4))
        for i, p in enumerate(self.parent):
            W[i] = locals_[i] if p < 0 else W[p] @ locals_[i]
        return W

    def rest_locals(self) -> np.ndarray:
        return np.stack([compose(*r) for r in self.rest])

    def rebased(self, full: "Skeleton") -> "Skeleton | None":
        """This (a model's embedded) skeleton re-parented on `full` (the unit's .skeleton file) when every bone of this one
        is in `full` and `full` holds bones this one lacks above them: the hierarchy and rest pose come from `full`, the
        inverse world transforms of the bones this one has stay its own (they match the mesh's vertices), the extra
        bones get `full`'s. None when nothing would change. D2R poses a unit on its skeleton file: Mephisto's model
        skeleton drops root/translate/anim/cog, so its spine_01 and pelvis become roots there and the hover height
        carried by `cog` is lost (calib r3: the game's bone palette matches the file skeleton to 0.006)."""
        if not set(self.names) <= set(full.names) or len(full.names) == len(self.names) and all(
                (self.parent[i] < 0) == (full.parent[full.idx[n]] < 0) for i, n in enumerate(self.names)):
            return None
        out = Skeleton.__new__(Skeleton)
        out.name = full.name
        out.names = list(full.names)
        out.parent = list(full.parent)
        out.idx = dict(full.idx)
        out.rest = list(full.rest)
        out.iwt = np.array(full.iwt)
        for n, i in self.idx.items():
            out.iwt[out.idx[n]] = self.iwt[i]
        return out

    def bind_error(self) -> float:
        """Largest |W_rest IWT - I| over all bones: 0 when the rest pose is the bind pose."""
        S = self.worlds(self.rest_locals()) @ self.iwt
        return float(np.abs(S - np.eye(4)).max())


@dataclass
class Pose:
    worlds: np.ndarray
    clip: str = ""
    time: float = 0.0
    unsupported: dict = field(default_factory=dict)    # curve kind -> number of track parts that kept the rest value
    tracks: int = 0


@dataclass
class Clip:
    name: str
    duration: float
    tracks: dict          # bone name -> TransformTrack object
    _dec: dict = field(default_factory=dict, repr=False)

    def curve(self, bone: str, part: str, dim: int):
        key = (bone, part)
        if key not in self._dec:
            try:
                self._dec[key] = curves.decode(self.tracks[bone][part], dim)
            except curves.UnsupportedCurve as e:
                self._dec[key] = e
        return self._dec[key]

    def kinds(self) -> dict:
        out = {}
        for tr in self.tracks.values():
            for k in ("OrientationCurve", "PositionCurve", "ScaleShearCurve"):
                kd = curve_kind(tr[k])
                out[kd] = out.get(kd, 0) + 1
        return out

    def decodable(self) -> bool:
        return all(k in SUPPORTED for k in self.kinds())


def load_clips(data: bytes, skeleton: Skeleton | None = None) -> dict:
    """name -> Clip of a .animations file; per clip the track group whose names best match the skeleton's bones."""
    _g, tree = load_tree(data)
    out = {}
    for an in tree["Animations"]:
        groups = an["TrackGroups"]
        best, score = None, -1
        for tg in groups:
            names = {tr["Name"] for tr in tg["TransformTracks"]}
            s = len(names & set(skeleton.names)) if skeleton else len(names)
            if s > score:
                best, score = tg, s
        tracks = {tr["Name"]: tr for tr in best["TransformTracks"]} if best is not None else {}
        out[an["Name"]] = Clip(an["Name"], float(an["Duration"]), tracks)
    return out


def locals_at(sk: Skeleton, clip: Clip | None, t: float, keep_root_translation: bool = False, method: str = "bspline",
              loop: bool = False):
    """(list of (pos, quat, ss) per bone, time used, unsupported kinds, tracks used) of `clip` at time t.
    t is clamped to the clip, or wrapped when loop (curves then continue across the end, see curves.py). A part a track
    does not animate (or cannot be decoded) keeps the bone's rest value. The engine ignores the root bone's translation
    (engine rule E1: a dropped root did not move the body), so the root keeps its rest position unless
    keep_root_translation."""
    out, unsupported, used = [], {}, 0
    if clip is None:
        t = 0.0
    elif loop and clip.duration > 0:
        t = t % clip.duration
    else:
        t = min(max(t, 0.0), clip.duration)
    for i, name in enumerate(sk.names):
        pos, quat, ss = sk.rest[i]
        tr = clip.tracks.get(name) if clip else None
        if tr is not None:
            used += 1
            parts = [("PositionCurve", 3, np.zeros(3), pos), ("OrientationCurve", 4, np.array([0, 0, 0, 1.0]), quat),
                     ("ScaleShearCurve", 9, np.eye(3).ravel(), ss)]
            vals = []
            for key, dim, ident, restv in parts:
                c = clip.curve(name, key, dim)
                if isinstance(c, Exception):
                    unsupported[str(c)] = unsupported.get(str(c), 0) + 1
                    vals.append(restv)
                    continue
                v = curves.sample(c, t, method=method, loop=loop, duration=clip.duration)
                vals.append(ident if v is None else v[:dim])
            pos, quat, ss = vals
            if sk.parent[i] < 0 and not keep_root_translation:
                pos = sk.rest[i][0]
        out.append((np.asarray(pos, float), np.asarray(quat, float), np.asarray(ss, float).ravel()))
    return out, t, unsupported, used


def blend_locals(a, b, w: float):
    """Local-space blend of two bone lists (crossfade weight w of b): lerp position and scale/shear, nlerp the rotation
    in the shorter hemisphere."""
    out = []
    for (pa, qa, sa), (pb, qb, sb) in zip(a, b):
        if float(np.dot(qa, qb)) < 0:
            qb = -qb
        q = (1 - w) * qa + w * qb
        out.append(((1 - w) * pa + w * pb, q / max(float(np.linalg.norm(q)), 1e-12), (1 - w) * sa + w * sb))
    return out


def pose_from_locals(sk: Skeleton, loc, clip: str = "", t: float = 0.0, unsupported=None, used: int = 0) -> Pose:
    return Pose(sk.worlds(np.stack([compose(*x) for x in loc])), clip, t, dict(unsupported or {}), used)


def pose_at(sk: Skeleton, clip: Clip | None, t: float, keep_root_translation: bool = False, method: str = "bspline",
            loop: bool = False) -> Pose:
    """World matrices of every bone with `clip` at time t (see locals_at). No clip: the rest pose."""
    loc, t, uns, used = locals_at(sk, clip, t, keep_root_translation, method, loop)
    return pose_from_locals(sk, loc, clip.name if clip else "", t, uns, used)


# ---- meshes ------------------------------------------------------------------------------------------------------------------

@dataclass
class Mesh:
    name: str
    pos: np.ndarray
    nrm: np.ndarray
    tan: np.ndarray
    uv: np.ndarray
    tris: np.ndarray
    maps: dict                        # usage -> logical texture path (first material group)
    bones: list                       # bone binding names
    joints: np.ndarray | None = None
    weights: np.ndarray | None = None
    params: dict = field(default_factory=dict)   # material: alpha_cut, blend, emissive, double_sided (material_params)
    col: np.ndarray | None = None     # (N, 4) uint8 vertex colours (DiffuseColor0), when the mesh has them
    groups: list = field(default_factory=list)   # every material group: (tri_first, tri_count, maps, params); maps and
    #                                              params above are the first group's. The game draws each group on its
    #                                              own with its own textures (Mephisto's body_meshShape: 3 draws, body_2,
    #                                              bones, eyes_head_2; RenderDoc calib r3 eids 24399/24407/24415).


def material_params(material) -> dict:
    """How a D2R material draws, from its ExtendedData: AlphaThreshold (or PrimaryAlphaThreshold when enabled) -> alpha_cut;
    AlphaIntensity -> blend (alpha-blended, alpha x intensity; with a threshold too, the cut wins); EmissiveIntensity ->
    emissive (x (1 - ORM alpha), as the game's ps_models); IsDoubleSided."""
    out = {}
    if material is None or material.get("ExtendedData") is None:
        return out
    ed = material["ExtendedData"].obj
    try:
        keys = set(ed.f)
    except AttributeError:
        return out
    g = ed.get
    if "AlphaThreshold" in keys and g("AlphaThreshold") is not None:
        out["alpha_cut"] = float(g("AlphaThreshold"))
    elif g("PrimaryAlphaThresholdEnabled"):
        out["alpha_cut"] = float(g("PrimaryAlphaThreshold") or 0.5)
    if "AlphaIntensity" in keys and "alpha_cut" not in out and g("AlphaIntensity") is not None:
        out["blend"] = float(g("AlphaIntensity"))
    if "EmissiveIntensity" in keys and g("EmissiveIntensity"):
        out["emissive"] = float(g("EmissiveIntensity"))
    if g("IsDoubleSided") if "IsDoubleSided" in keys else False:
        out["double_sided"] = True
    if "technique" in keys and str(g("technique")) == "WaterMaterial":     # the game's water_p (lane r3-calibfix)
        out["water"] = {k: (list(map(float, g(k))) if isinstance(g(k), (list, tuple)) else float(g(k)))
                        for k in ("ShallowAlbedo", "DeepAlbedo", "ShallowAlpha", "DeepAlpha", "Roughness", "FoamAlbedo",
                                  "WaterTransmissionDistanceMax", "ShoreFoamDepth", "ShoreFoamTextureScale",
                                  "WaterFlowNormalIntensity", "WaterFlowNormalTextureScale",
                                  "WaterFlowNormalDetailTextureScale", "WaterFlowNormalDetailIntensity",
                                  "WaterFlowNormalSpeed", "WaterFlowIntensity", "WaterFlowDirection",   # the flow (r4-glow)
                                  "ShoreFoamAnimationSpeed", "UseFlowTexture")
                        if k in keys and g(k) is not None}
    return out


def load_meshes(data: bytes):
    """([Mesh], Skeleton of the file or None) of a .model."""
    _g, tree = load_tree(data)
    meshes = []
    for m in tree["Meshes"]:
        d = hd_gltf.decode_mesh(m)
        groups = []
        for (mat, first, count) in d["groups"]:
            gm, gp = {}, {}
            if mat < len(m["MaterialBindings"]):
                gm = {k: v for k, v in hd_gltf._maps(m["MaterialBindings"][mat]["Material"]).items() if v}
                gp = material_params(m["MaterialBindings"][mat]["Material"])
            groups.append((int(first), int(count), gm, gp))
        maps, prm = (groups[0][2], groups[0][3]) if groups else ({}, {})
        meshes.append(Mesh(m["Name"], d["pos"], d["nrm"], d["tan"], d["uv"].astype(float), d["tris"], maps,
                           [b["BoneName"] for b in m["BoneBindings"]],
                           d.get("joints"), None if d.get("weights") is None else d["weights"] / 255.0, prm,
                           d.get("col"), groups))
    sks = tree["Skeletons"]
    return meshes, (Skeleton(sks[0]) if sks else None)


def skin(mesh: Mesh, sk: Skeleton | None, pose: Pose | None):
    """(pos, nrm, tan) of the mesh in model space for the pose; unskinned meshes (or no pose) come back as they are."""
    if mesh.joints is None or sk is None or pose is None:
        return mesh.pos, mesh.nrm, mesh.tan
    S = pose.worlds @ sk.iwt                                    # (B, 4, 4)
    remap = np.array([sk.idx.get(n, 0) for n in mesh.bones], int)
    J = remap[mesh.joints]
    P = np.hstack([mesh.pos, np.ones((len(mesh.pos), 1))])
    pos = np.zeros((len(P), 3))
    R = np.zeros((len(P), 3, 3))
    for k in range(4):
        Mk = S[J[:, k]]
        w = mesh.weights[:, k, None]
        pos += w * np.einsum("nij,nj->ni", Mk, P)[:, :3]
        R += w[..., None] * Mk[:, :3, :3]
    nrm = np.einsum("nij,nj->ni", R, mesh.nrm)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    t3 = np.einsum("nij,nj->ni", R, mesh.tan[:, :3])
    t3 /= np.maximum(np.linalg.norm(t3, axis=1, keepdims=True), 1e-12)
    return pos, nrm, np.hstack([t3, mesh.tan[:, 3:4]])
