"""An effect spawned by a unit's animation timeline, placed as the game places it (lane hdfx-r3-decal2).

D2R's `combined.timelines` SpawnVFX events (context.timeline_bindings) start an effect at a clip time on a bone; with
`attachToEntity` or `attachToDeadBody` it follows that bone, and a death clip ends in the corpse pose the unit keeps (the
`dead` mode). Blood pools (`vfx_bloodlord1_bloodpool`: death clip time 0.5 s, bone `root`, attachToDeadBody) therefore
start part-way through the death animation and lie where the corpse's bone is, not at the time of the kill.

`TimelineEffect.of(fs, unit_name, unit, effect, mode, rate, t0)` finds the event of `effect` in the unit's `mode`
timeline; `start` is the effect time (seconds after the clip's time `t0`) at which it spawns, `clip_time(t)` the clip
time at effect time t (t0 + t x rate, held at the clip's end: a death plays once), `driver()` a context.AttachDriver
whose time is the effect's own age (for view/fxcli.drive_to; `place` = the effect transform the caller multiplies in).
The clip rate is the game's: the HD clip is stretched over the classic mode's frames (context.event_time); callers pass
it (fxcli --unit-rate / --unit-period, measured from shots).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import context as C


class ClipPoses:
    """Bone world matrices of one clip played once from `t0` at `rate` clip seconds per second, held at its end."""

    def __init__(self, skeleton, clip, rate: float = 1.0, t0: float = 0.0, pose_kw: dict | None = None):
        self.sk, self.clip, self.rate, self.t0 = skeleton, clip, float(rate), float(t0)
        self.pose_kw = dict(pose_kw or {})
        self.pose_kw["loop"] = False
        self.duration = float(getattr(clip, "duration", 0.0) or 0.0)
        self._cache: dict = {}

    def clip_time(self, t: float) -> float:
        return min(max(self.t0 + t * self.rate, 0.0), self.duration)

    def worlds(self, t: float) -> np.ndarray:
        ct = round(self.clip_time(t), 4)
        if ct not in self._cache:
            from ..view import gr2
            self._cache[ct] = gr2.pose_at(self.sk, self.clip, ct, **self.pose_kw).worlds
        return self._cache[ct]

    def index(self, bone: str) -> int:
        return C._bone_index(self.sk, bone)


class _Attached(C.AttachDriver):
    """AttachDriver whose unit frame is the root scale only: the caller's effect transform (yaw, place) is multiplied in
    by view/fxcli.drive_to as `driver.place`."""

    def _unit_frame(self, t):
        return self.R


@dataclass
class TimelineEffect:
    binding: object                # context.Binding (trigger "timeline")
    start: float                   # effect seconds after clip time t0 at which the event fires
    poses: object                  # ClipPoses (effect time) or None
    bone: str | None               # bone the effect follows (None: the unit's frame)
    follow: bool
    unit_scale: float = 1.0

    @classmethod
    def of(cls, fs, unit_name: str, unit, effect: str, mode: str = "dt", rate: float = 1.0, t0: float = 0.0,
           clip=None, tables=None) -> "TimelineEffect":
        """The event of `effect` in `unit_name`'s timeline for `mode` (a mode code, dt = death, or a timeline name)."""
        from ..view import vfs
        ue = C.effects_of_unit(fs, unit_name, tables)
        want = vfs.normalise(effect)
        mode_l = C.DEFAULT_MODE_MAP.get(mode.lower(), mode.lower())
        binds = [b for b in ue.timeline(mode_l) if vfs.normalise(b.file) == want] or \
                [b for b in ue.timeline() if vfs.normalise(b.file) == want]
        if not binds:
            raise KeyError("%s: no timeline event of %s in %s" % (unit_name, effect, mode))
        b = binds[0]
        sk = None
        if unit is not None:
            sk = next((p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None), None)
        poses = ClipPoses(sk, clip, rate, t0) if sk is not None and clip is not None else None
        bone = b.bone
        if bone and (bone.lower() == "root" or b.extra.get("unit_part")):
            bone = None                      # `root` is the unit's own frame (context.timeline_children)
        if bone and poses is not None:
            try:
                poses.index(bone)
            except KeyError:
                bone = None
        follow = bool(b.attach or b.extra.get("attach_to_dead_body") or b.attach is None)
        scale = float(getattr(unit, "root_scale", 1.0) or 1.0) if unit is not None else 1.0
        return cls(b, event_start(b.time or 0.0, rate, t0), poses, bone, follow, scale)

    def clip_time(self, t: float) -> float:
        return self.poses.clip_time(t) if self.poses is not None else t

    def driver(self, duration: float = 30.0):
        """An AttachDriver whose time is the effect's age (0 at the event). A non-following effect stays where the bone
        was at the event."""
        lay = C.Layer(self.binding.file, self.binding.xf, self.bone, self.binding.inherit_only_position, "timeline")
        poses = self.poses if self.poses is not None else _NoPoses()
        d = _Attached([lay], poses, self.unit_scale, None, 0.0, 0.0, duration, None, "timeline", pose_t0=-self.start)
        if not self.follow:
            M = d.layer_transform(0.0, lay)
            d = C.StaticDriver(C.Layer(self.binding.file, np.eye(4), None, False, "timeline"), M, 0.0, duration)
        return d


def event_start(event_clip_time: float, rate: float = 1.0, t0: float = 0.0) -> float:
    """Effect seconds after clip time t0 at which a clip-time event fires (the clip runs at `rate`)."""
    return (float(event_clip_time) - float(t0)) / max(float(rate), 1e-6)


class _NoPoses:
    duration = 0.0

    def worlds(self, t):
        return np.eye(4)[None]

    def index(self, bone):
        raise KeyError(bone)
