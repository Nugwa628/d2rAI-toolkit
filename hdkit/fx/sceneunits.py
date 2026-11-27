"""The units round an effect: extra units placed in the viewer's scene, and the pose each one plays (lane r3-aura2).

Why: the game's aura stencil (fx/aurastencil.py) hides an aura over EVERY unit model drawn in the depth prepass, the
player and every monster, alive or dead (RenderDoc, lane r3-aurastencil run a2, frame 9038: the four Fallen torsos, three
of them corpses, write 0x10 like the player's body). The viewer drew one `--unit`; this module adds the others, so their
draws mark the stencil too (light class "unit", picked up by aurastencil.units_of).

A scene-units file (JSON) lists them:

    {"units": [{"unit": "hd/character/enemy/fallen1.json",     # unit JSON or .model
                "at": [2, -1],            # harness sub-tiles from the effect's unit (world x = 2 dx, z = 2 dy), or
                "pos": [x, y, z],         # world units (overrides "at")
                "yaw": 225.0,             # camera.rot_y degrees (or "facing": key 0..7)
                "mode": "a1",             # unit mode token through the unit's state machine (view/player.py), or
                "clip": "attack1",        # a clip name; neither: the bind pose
                "wclass": "hth", "t0": 0.0, "rate": 1.0, "period": null,   # clip time = t0 + t x rate (period: loop s)
                "scale": 1.0,             # on top of the unit JSON's root scale, or
                "world_scale": 1.1,       # the whole scale (a capture's world matrix: replaces the root scale)
                "hide": []}]}

`mode_clip(fs, unit_logical, unit, mode, wclass)` is the pose selection both the main `--unit` (fx CLI `--unit-mode`) and
the extra units use: the unit's AnimationStateMachine (player.StateMachine) names the clip of a mode for a weapon class.
`from_capture(draw_dirs, ref)` writes the "pos"/"yaw" of units from RenderDoc model draws (calib.gamecam world matrices),
relative to the reference unit's matrix (the effect's unit), so a game capture's scene can be rebuilt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

UNITS_PER_SUBTILE = 2.0


@dataclass
class SceneUnit:
    unit: str
    pos: tuple = (0.0, 0.0, 0.0)
    yaw: float | None = None
    facing: int = 0
    mode: str | None = None
    clip: str | None = None
    wclass: str = "hth"
    t0: float = 0.0
    rate: float = 1.0
    period: float | None = None
    scale: float = 1.0
    world_scale: float | None = None
    hide: list = field(default_factory=list)
    name: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SceneUnit":
        d = dict(d)
        if "pos" in d:
            pos = tuple(float(v) for v in d.pop("pos"))
        else:
            at = d.pop("at", (0.0, 0.0))
            pos = (UNITS_PER_SUBTILE * float(at[0]), float(at[2]) if len(at) > 2 else 0.0, UNITS_PER_SUBTILE * float(at[1]))
        d.pop("at", None)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(pos=pos, **known)


def load(path_or_doc) -> list:
    """[SceneUnit] of a scene-units file (path, JSON text or dict; a bare list is accepted too)."""
    doc = path_or_doc
    if isinstance(doc, (str, Path)) and not str(doc).lstrip().startswith(("{", "[")):
        doc = Path(doc).read_text(encoding="utf-8")
    if isinstance(doc, str):
        doc = json.loads(doc)
    items = doc.get("units", []) if isinstance(doc, dict) else doc
    return [SceneUnit.from_dict(it) for it in items]


def state_machine(fs, unit_logical: str):
    """The unit's player.StateMachine (UnitRootComponent.state_machine_filename), or None."""
    from ..view import player
    if not str(unit_logical).lower().endswith(".json"):
        return None
    u = json.loads(fs.read(unit_logical).decode("utf-8"))
    smf = None
    for e in u.get("entities", []):
        for c in e.get("components", []):
            if c.get("type") == "UnitRootComponent" and c.get("state_machine_filename"):
                smf = c["state_machine_filename"]
    if not smf:
        return None
    for logical in (smf, smf[len("data/"):] if smf.lower().startswith("data/") else smf):
        try:
            return player.StateMachine.from_json(fs.read(logical))
        except (FileNotFoundError, KeyError, OSError):
            continue
    return None


def mode_clip(fs, unit_logical: str, unit, mode: str, wclass: str = "hth") -> tuple:
    """(clip name, note) a unit plays in `mode` (token nu, wl, a1, dd ...) with weapon class `wclass`: the state machine's
    binding (its `hth` binding, else the first, when the class has none); without a state machine, the clip named like the
    mode (scene.MODE_CLIP, '<wclass>_<clip>' for player units)."""
    from ..view import scene
    sm = None
    try:
        sm = state_machine(fs, unit_logical)
    except (FileNotFoundError, ValueError):
        sm = None
    clips = getattr(unit, "clips", {}) or {}
    if sm is not None:
        try:
            name = sm.clip_name(mode, wclass)
            if name in clips:
                return name, "state machine %s: mode %s, class %s -> %s" % (sm.name, mode, wclass, name)
            hit = [c for c in clips if c.lower() == name.lower()]
            if hit:
                return hit[0], "state machine %s: mode %s -> %s" % (sm.name, mode, hit[0])
        except KeyError:
            pass
    base = scene.MODE_CLIP.get(mode.lower(), mode)
    for cand in ("%s_%s" % (wclass, base), base):
        if cand in clips:
            return cand, "no state machine binding: clip named %s" % cand
    return None, "no clip for mode %s (class %s): bind pose" % (mode, wclass)


def clip_time(spec_t0: float, rate: float, t: float) -> float:
    return float(spec_t0) + float(t) * float(rate)


def place_matrix(su: SceneUnit) -> np.ndarray:
    M = np.eye(4)
    M[:3, 3] = su.pos
    return M


def prepare(R, fs, specs, cam, t: float, lod: int = 0, cache: dict | None = None, log=None) -> tuple:
    """(draws, notes) of the extra units at effect time t: each loaded (cached by path), posed (mode or clip at
    t0 + t x rate, looping), turned by its yaw, scaled, placed at its position; light class 'unit'."""
    from ..view import camera, scene
    cache = {} if cache is None else cache
    draws, notes = [], []
    for k, su in enumerate(specs):
        key = (su.unit, lod)
        if key not in cache:
            cache[key] = scene.load_unit(fs, su.unit, lod)
        u = cache[key]
        clip, how = su.clip, "clip %s" % su.clip
        if su.mode and not clip:
            clip, how = mode_clip(fs, su.unit, u, su.mode, su.wclass)
        rate = su.rate
        if su.period and clip in u.clips:
            rate = float(u.clips[clip].duration) / float(su.period)
        yaw = su.yaw if su.yaw is not None else camera.facing_yaw(camera.key_dir64(su.facing))
        root = u.root_scale
        u.root_scale = su.world_scale if su.world_scale is not None else root * su.scale
        try:
            prep = R.prepare(u, clip, clip_time(su.t0, rate, t), yaw, cam, pose_kw={"loop": True},
                             place=place_matrix(su), light_class="unit").select(su.hide)
        finally:
            u.root_scale = root
        draws += prep.draws
        notes.append("unit %d %s at %s yaw %.1f: %s" % (k, su.name or Path(su.unit).stem, [round(v, 2) for v in su.pos],
                                                       yaw, how))
    return draws, notes


def from_capture(draws: dict, ref_dir, modes: dict | None = None) -> dict:
    """A scene-units document from RenderDoc model draws (rd_extract draws/<eid>/ folders): `draws` maps a draw folder to
    its unit (logical path); positions and yaws come from each draw's world matrix (calib.gamecam.world_matrix /
    placement), relative to the reference draw `ref_dir` (the effect's unit: its position is the viewer's origin).
    `modes` maps a draw folder to its mode token (e.g. 'dd' for a corpse)."""
    from .calib import gamecam
    ref = gamecam.placement(gamecam.world_matrix(ref_dir))
    o = np.asarray(ref["position"], float)
    out = []
    for d, unit in draws.items():
        p = gamecam.placement(gamecam.world_matrix(d))
        rel = (np.asarray(p["position"], float) - o).round(4).tolist()
        it = {"unit": unit, "pos": rel, "yaw": p["yaw"], "world_scale": p["scale"][1], "name": Path(d).name}
        if modes and d in modes:
            it["mode"] = modes[d]
        out.append(it)
    return {"units": out, "ref": {"draw": str(ref_dir), "position": ref["position"], "yaw": ref["yaw"]}}
