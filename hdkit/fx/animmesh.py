"""Skinned FX meshes: the mesh renderer feature `Animation` (lane r3-animuv).

What D2R does (decrypted image, build 93847): the Standard/Default/Stencil/Uber mesh class reader (0x1410385b0 for
`Diablo_Standard_Mesh`; 0x14100e8b0 / 0x141027db0 for the others) reads `Animation` (on/off), `Animation.Skeleton`
(+0x1d0), `Animation.AnimationFile` (+0x1d8), `Animation.Speed` (+0x1e0, float) and `Animation.LoopCount` (+0x1e4, int)
into the renderer, and the vertex shader runs with SKINNING_MODE on (docs/hdfx-shaders.md 2). The model instance is
created by the layer's spawn script (`_pksi_vvCreateAnimatedModelInstance`, e.g. Bone Spirit object 190), so the clip
starts when the particle is born: clip time = particle age x Speed.

Files: the `.animation` a renderer names is not shipped on its own; the clip of that name (the file stem, compared
without case) lives in `<folder>/combined.animations` next to it, as for units (`scene.load_unit`). Bone Spirit:
`bonespirit.animation` -> clip `bonespirit` (0.6 s); vine beast: 10 clips in one file.

LoopCount: the number of plays, then the last frame holds; 0 plays forever (horse `neutral` and the eruption cracks'
idle, LoopCount 0; deaths, starts and stops 1). GUESS from the corpus pattern; the field's consumer in the model
renderer is not read.

Skinning: the model's own skeleton (its inverse world transforms match its vertices), re-parented on the
`Animation.Skeleton` file like a unit (gr2.Skeleton.rebased); the root keeps its translation (an FX clip moves the
whole piece: ceiling chunks, eruption-crack slabs), unlike a unit's walk cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

import numpy as np

AGE_STEP = 1.0 / 120.0     # particles whose clip times round to the same step share one skinning


@dataclass
class AnimSpec:
    skeleton: str | None
    clip_file: str
    speed: float = 1.0
    loops: int = 1

    @property
    def combined(self) -> str:
        return str(PurePosixPath(self.clip_file).parent / "combined.animations")

    @property
    def clip_name(self) -> str:
        return PurePosixPath(self.clip_file).stem


def _num(v, d):
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def spec_of(r) -> AnimSpec | None:
    """The renderer's animation (an RSpec / anything with .get), or None when Animation is off or has no clip."""
    if not r.get("Animation"):
        return None
    f = r.get("Animation.AnimationFile")
    if not f or not isinstance(f, str):
        return None
    return AnimSpec(r.get("Animation.Skeleton") or None, f, _num(r.get("Animation.Speed"), 1.0),
                    int(_num(r.get("Animation.LoopCount"), 1)))


def clip_time(age, duration: float, speed: float = 1.0, loops: int = 1) -> np.ndarray:
    """Clip time per particle for its age (s): age x speed, wrapped while plays remain; after `loops` plays the last
    frame holds; loops 0 wraps forever."""
    t = np.maximum(np.asarray(age, float), 0.0) * float(speed)
    if duration <= 0:
        return np.zeros_like(t)
    wrapped = np.mod(t, duration)
    if loops <= 0:
        return wrapped
    return np.where(t >= loops * duration, duration, wrapped)


def particle_age(fields: dict | None, n: int) -> np.ndarray:
    """Age (s) of each particle from the batch streams self.lifeRatio / self.invLife (zeros when absent)."""
    if not fields:
        return np.zeros(n)
    lr, il = fields.get("self.lifeRatio"), fields.get("self.invLife")
    if lr is None or il is None:
        return np.zeros(n)
    lr = np.asarray(lr, float).reshape(-1)[:n]
    il = np.asarray(il, float).reshape(-1)[:n]
    age = np.where(il > 0, lr / np.where(il > 0, il, 1.0), 0.0)
    return np.pad(age, (0, n - len(age))) if len(age) < n else age


class Animator:
    """Loads and caches skeletons and clips through a vfs; skins a model part's meshes at a clip time."""

    def __init__(self, vfs, notes: list | None = None):
        self.vfs = vfs
        self.notes = notes if notes is not None else []
        self._sk: dict = {}
        self._clips: dict = {}
        self._skin: dict = {}

    def _file_skeleton(self, path):
        from ..view import gr2
        if path not in self._sk:
            sk = None
            try:
                if path and self.vfs.exists(path):
                    _g, t = gr2.load_tree(self.vfs.read(path))
                    sk = gr2.Skeleton(t["Skeletons"][0])
            except (KeyError, IndexError, ValueError) as e:
                self.notes.append("animation skeleton %s: %s" % (path, e.__class__.__name__))
            self._sk[path] = sk
        return self._sk[path]

    def skeleton(self, part_sk, spec: AnimSpec):
        """The skeleton to pose: the model's own, re-parented on the Animation.Skeleton file when that adds bones."""
        full = self._file_skeleton(spec.skeleton)
        if part_sk is None:
            return full
        if full is not None:
            rb = part_sk.rebased(full)
            if rb is not None:
                return rb
        return part_sk

    def clip(self, spec: AnimSpec, sk):
        from ..view import gr2
        key = (spec.combined, spec.clip_name.lower(), id(sk))
        if key not in self._clips:
            c = None
            try:
                if self.vfs.exists(spec.combined):
                    clips = gr2.load_clips(self.vfs.read(spec.combined), sk)
                    c = next((v for k, v in clips.items() if k.lower() == spec.clip_name.lower()), None)
                    if c is None:
                        self.notes.append("animation %s: no clip %s in %s; bind pose" % (spec.clip_file, spec.clip_name,
                                                                                         sorted(clips)))
                else:
                    self.notes.append("animation %s: %s not found; bind pose" % (spec.clip_file, spec.combined))
            except (KeyError, IndexError, ValueError) as e:
                self.notes.append("animation %s: %s; bind pose" % (spec.clip_file, e.__class__.__name__))
            self._clips[key] = c
        return self._clips[key]

    def duration(self, spec: AnimSpec, part_sk) -> float:
        sk = self.skeleton(part_sk, spec)
        c = self.clip(spec, sk) if sk is not None else None
        return float(c.duration) if c is not None else 0.0

    def skinned(self, meshes, part_sk, spec: AnimSpec, t: float) -> list:
        """[(pos, nrm)] per mesh at clip time t (model space)."""
        from ..view import gr2
        sk = self.skeleton(part_sk, spec)
        clip = self.clip(spec, sk) if sk is not None else None
        if sk is None or clip is None:
            return [(m.pos, m.nrm) for m in meshes]
        tq = round(float(t) / AGE_STEP) * AGE_STEP
        key = (id(meshes), spec.combined, spec.clip_name, tq)
        hit = self._skin.get(key)
        if hit is None:
            pose = gr2.pose_at(sk, clip, tq, keep_root_translation=True)
            hit = [gr2.skin(m, sk, pose)[:2] for m in meshes]
            if len(self._skin) > 4096:
                self._skin.clear()
            self._skin[key] = hit
        return hit


def time_groups(times: np.ndarray):
    """[(clip time, particle index array)] grouping particles whose times round to the same AGE_STEP."""
    q = np.round(np.asarray(times, float) / AGE_STEP).astype(np.int64)
    out = []
    for k in np.unique(q):
        out.append((float(k) * AGE_STEP, np.nonzero(q == k)[0]))
    return out


# ---- the render hook (fx/render.py FxRenderer._prep_mesh) -----------------------------------------------------------

def skin_groups(fxr, r, path, meshes, n, batch):
    """None when the renderer has no Animation, else [(clip time, particle indices, [(pos, nrm)] per mesh)].
    `fxr` is the FxRenderer (its vfs, notes and the model part's skeleton cache)."""
    spec = spec_of(r)
    if spec is None:
        return None
    a = getattr(fxr, "_animator", None)
    if a is None:
        a = fxr._animator = Animator(fxr.vfs, fxr.notes)
    part_sk = getattr(fxr, "_mesh_sk", {}).get(path)
    dur = a.duration(spec, part_sk)
    age = particle_age(getattr(batch, "fields", None), n)
    times = clip_time(age, dur, spec.speed, spec.loops)
    return [(t, idx, a.skinned(meshes, part_sk, spec, t)) for t, idx in time_groups(times)]


def pack_groups(groups, mi, m, X, pblock, va, dm):
    """pack_mesh of mesh `mi` for each time group with its skinned vertices and its particles."""
    from . import packers as pk
    pb = np.asarray(pblock, np.float32)
    out = []
    for _t, idx, sk in groups:
        p, nrm = sk[mi]
        pbi = pb[idx] if len(pb) == len(X) else np.broadcast_to(pb[:1], (len(idx), pb.shape[1]))
        out.append(pk.pack_mesh(p, nrm, m.uv, m.tris, X[idx], pbi, va, dm))
    return np.concatenate(out) if out else np.zeros((0, pk.FLOATS), np.float32)
