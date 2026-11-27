"""Effects on the moving bones of a posed unit (lane hdfx-r3-shapes).

The game plays a unit's effect entities on their bones (`EntityAttachmentDefinitionComponent.targetbone`) and moves each
effect instance with its bone every frame. Effects whose particles are placed through the instance transform (the
scripts' `xform*` calls, the spawn-time transform, `dxformdt` velocity) therefore form their shape from the bone's
motion: the Wraith's wing trails (`vfx_wraith1_neutral`, bone `spine_spine05`; its curve ribbons hang 4.5 units down
in the bone's frame), the Pain Worm's chest and pelvis glows. Before this module `python -m hdkit.view fx --unit` put
the effect at the unit's ORIGIN, turned only by the unit's yaw: bone-attached effects drew in the wrong place (the
Wraith's trails below the floor) and never moved.

`bone_drivers(fs, unit, effect_path, ...)` finds the effect's bindings on the unit (`fx.context.effects_of_unit`: the
entities and object effects that draw while the unit lives) and returns one `BoneDriver` per binding. A BoneDriver is a
`context.AttachDriver` whose poses are `ClipPoses`: the same `gr2.pose_at(skeleton, clip, t0 + t x rate)` the unit's own
draw uses (`gpu.Renderer.prepare`), so the effect sits on the bone of the drawn pose at every sim step. The world matrix
is `outer x rot_y(yaw) x root_scale x bone world x entity transform`, as the unit draw (`outer` = the CLI's lift place).
`fxcli.drive_to` steps each Sim with its driver (`sim_dt` steps), moving the transform and the velocity before each step.
"""
from __future__ import annotations

import numpy as np

from ..fx import context as C


class ClipPoses:
    """Bone world matrices of a unit's clip over effect time: worlds(t) = pose at clip time t0 + t x rate (looped, as the
    fx CLI draws the unit). `poser(skeleton, clip_time) -> gr2.Pose` replaces gr2.pose_at (the capture's root offsets)."""

    def __init__(self, skeleton, clip=None, t0: float = 0.0, rate: float = 1.0, pose_kw: dict | None = None,
                 poser=None, quantum: float = 1e-4):
        self.sk, self.clip, self.t0, self.rate = skeleton, clip, float(t0), float(rate)
        self.pose_kw = dict(pose_kw or {"loop": True})
        self.poser = poser
        self.q = float(quantum)
        self.duration = 1e9                          # a looping clip never ends
        self._cache: dict = {}

    def clip_time(self, t: float) -> float:
        return self.t0 + float(t) * self.rate

    def worlds(self, t: float) -> np.ndarray:
        k = int(round(self.clip_time(t) / self.q))
        w = self._cache.get(k)
        if w is None:
            ct = k * self.q
            if self.poser is not None:
                w = self.poser(self.sk, ct).worlds
            else:
                from . import gr2
                w = gr2.pose_at(self.sk, self.clip, ct, **self.pose_kw).worlds
            if len(self._cache) > 4096:
                self._cache.clear()
            self._cache[k] = w
        return w

    def index(self, bone: str) -> int:
        return C._bone_index(self.sk, bone)


class BoneDriver(C.AttachDriver):
    """AttachDriver for `fxcli.drive_to`: that helper left-multiplies `driver.place`, so `place` stays the identity and
    the unit's world placement (the CLI's lift) is `outer`."""
    kind = "bone"

    def __init__(self, layers, poses, unit_scale=1.0, outer=None, yaw=0.0, name="bone"):
        super().__init__(layers, poses, unit_scale, None, yaw, 0.0, None, None, name, pose_t0=0.0)
        self.outer = np.eye(4) if outer is None else np.asarray(outer, float)

    def _unit_frame(self, t):
        return self.outer @ self.R


def bindings_for(fs, unit: str, effect_path: str, skill: str | None = None) -> list:
    """The bindings of `effect_path` on `unit` (a unit name or JSON path) that draw while the unit lives (entity, object
    and name-bound overlay triggers), with a bone the unit's effect places on. [] when the effect is not bound to it."""
    from . import vfs
    try:
        ue = C.effects_of_unit(fs, unit)
    except (FileNotFoundError, KeyError, ValueError):
        return []
    want = vfs.normalise(effect_path)
    out = []
    for b in ue.vfx(skill):
        f = C.resolve_particle(fs, b.file) or b.file
        if vfs.normalise(f) == want:
            out.append(b)
    return out


def skeleton_of(unit):
    return next((p.skeleton for p in getattr(unit, "parts", []) if getattr(p, "skeleton", None) is not None), None)


def bone_drivers(fs, unit_json: str, unit, effect_path: str, clip=None, t0: float = 0.0, rate: float = 1.0,
                 yaw: float = 0.0, outer=None, pose_kw: dict | None = None, poser=None, skill: str | None = None) -> list:
    """[(label, BoneDriver)] for each binding of the effect on the unit (a `scene.Unit`; `unit_json` its JSON path),
    all sharing one ClipPoses. Bindings whose bone the skeleton lacks fall back to the unit frame (as
    `context.timeline_driver` does for `root`), listed in the driver's `skipped`."""
    sk = skeleton_of(unit)
    binds = bindings_for(fs, unit_json, effect_path, skill) if sk is not None else []
    if not binds:
        return []
    poses = ClipPoses(sk, clip, t0, rate, pose_kw, poser)
    scale = float(getattr(unit, "root_scale", 1.0) or 1.0)
    out = []
    for b in binds:
        lay = C.layers_of(b)
        skipped = []
        for i, l in enumerate(lay):
            if l.bone and l.bone.lower() != "root":
                try:
                    poses.index(l.bone)
                except KeyError:
                    skipped.append(l.bone)
                    lay[i] = C.Layer(l.effect, l.xf, None, l.inherit_only_position, l.label)
            elif l.bone:
                lay[i] = C.Layer(l.effect, l.xf, None, l.inherit_only_position, l.label)
        d = BoneDriver(lay, poses, scale, outer, yaw, name="bone:%s" % (b.bone or "root"))
        d.skipped = skipped
        d.binding = b
        out.append(("%s@%s" % (b.entity or "entity", b.bone or "root"), d))
    return out
