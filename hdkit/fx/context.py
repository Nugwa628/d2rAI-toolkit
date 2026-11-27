"""In-game context for effects: how the game binds a `.particles` effect to units, missiles, skills and maps, so the
simulator and renderer can show an effect as the game does (lane context of project hdfx; contract: docs/hdfx.md section 6).

Two halves.

1. What is bound where (data, read-only; the game's JSONs and the mod's tables):
     effects_of_unit(fs, unit)         entities of a unit JSON with a VfxDefinitionComponent: file, bone
                                       (EntityAttachmentDefinitionComponent.targetbone), transform (xf), skill filter
                                       (UnitSourceSkillFilterComponent), the unit's own light layers (ObjectEffect) and the
                                       hit effects of its DamageFXComponent
     effects_of_missile(fs, name, t)   missiles.txt name -> normalised key -> hd/missiles/missiles.json -> hd/missiles/<model>.json:
                                       its effect entities with the transforms the game applies, plus the row's velocity,
                                       acceleration, range, light and the missiles it makes (explosion, sub missiles)
     effects_of_overlay(fs, name, t)   overlay.txt row -> hd/overlays/<Character folder>/<key>.json
     effects_of_skill(fs, name, t)     skills.txt row -> the missiles and overlays its columns name, each resolved as above
   Tables (`Tables`) come from the caller (a folder with missiles.txt, skills.txt, overlay.txt, monstats.txt ...): this
   module knows no mod's paths. `fs` is a `hdkit.view.vfs.Layered` (or anything with exists/read/walk).

2. Drivers: objects that give a `SimContext` per frame, so `Sim(effect, ctx=driver.frame(t).ctx)` runs the effect where
   the game would put it.
     attach(effect, unit, bone, ...)   the effect follows a bone of a posed unit (hdkit.view.player poses per frame)
     missile(effect, start/end|path, ...)  flight with the row's speed, acceleration and range; hit / explode events spawn
                                       the explosion effect (a child driver)
     skill(effects..., unit, cast_frame, mode)  the cast: the unit plays the mode, the effects start at the trigger frame
     scene(map, xy)                    a place on an HD map: game light, bloom/tonemap preset, preset point lights, the frame
   `drive()` plays a driver against a Sim factory (children included).

Conventions (all assumptions are listed, none is verified in game unless said):
  - world units are the game's HD metres, y up; a classic sub-tile is 2 units, a cell 10 (`hdkit.view.mapview.frame`).
  - a missile or unit faces +z at yaw 0 (fire_arrow: effect at z +1, model at z -0.35: the head is +z); a world yaw is
    `camera.rot_y` degrees (positive turns +z towards +x).
  - missiles.txt `Vel` is MEASURED in game (lane calib2, 2026-11-25, server positions of missiles fired by the harness, D2R
    3.4 with a mod loaded): 1 Vel = 3/64 sub-tile per game frame at 25 frames per second, i.e. 2.34375 world units/s (Vel 12 over
    47-57 sub-tiles: 0.562 / 0.568 sub-tiles per frame; Vel 20: 0.93-0.96; Vel 36: 1.64). The community rule "32 pixels = 1
    sub-tile" (1.5625 units/s) is 1.5x too slow. `Range` is the lifetime in frames (checked: Fire Bolt, Range 50, dies 47-51
    frames after its launch; a skill may override it in the engine; not read here). `Accel` is added to the velocity every
    5th frame, up to `MaxVel` (read from the game's code: ACCEL_EVERY, ACCEL_VEL_PER_FRAME, VEL_MAX_PER_VEL).
  - a unit's attached effect gets bone-world x the entity's local transform, with `unit.root_scale` and the placement yaw as
    `gpu.Renderer.prepare` applies them to the model; `inheritOnlyPosition` keeps only the bone's translation.
  - `SimContext.ground_z` is the ground height in world y (0 on maps and for a lone unit).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

UNITS_PER_SUBTILE = 2.0
CELL = 10.0                      # world units per map cell (5 sub-tiles)
FPS = 25.0                       # the game's frame rate
VEL_SUBTILES_PER_FRAME = 3.0 / 64.0   # missiles.txt Vel 1, measured in game (lane calib2; module docstring)
VEL_UNITS_PER_S = FPS * UNITS_PER_SUBTILE * VEL_SUBTILES_PER_FRAME   # 2.34375 world units per second per Vel
# Missile motion, READ from the game's code (D2MOO, MIT: D2Game MISSILES_CreateMissileFromParams, D2Common Path
# PATH_SetVelocity / sub_6FD5CEB0; lane calib4): the path velocity is raw = Vel x 256 x 75 / 100 and a frame moves the
# missile raw / 4096 sub-tiles (= Vel x 3/64: the calib2 measurement, exactly). `Accel` is added to raw (not x 256, not
# x 0.75) once every ACCEL_EVERY frames (the 5th, 10th ... path update), clamped to [0, MaxVel x 256] (the cap has no
# 75 % cut: VEL_MAX_PER_VEL). In Vel units a step is Accel / 192, i.e. Accel / 960 Vel per frame on average
# (ACCEL_VEL_PER_FRAME; lane calib3 had fitted 0.7 per 1000 on one server track, which the stepped model also fits:
# Frost Nova Vel 24, Accel -1000 flies 4 frames at 1.125 sub-tiles, 5 at 0.879, 5 at 0.635: 12.1 sub-tiles in 14 frames).
ACCEL_EVERY = 5
ACCEL_VEL_PER_FRAME = 1.0 / 960.0
ACCEL_UNITS_PER_S2 = FPS * VEL_UNITS_PER_S * ACCEL_VEL_PER_FRAME
VEL_MAX_PER_VEL = 1.0 / 0.75     # MaxVel in our Vel units (Vel carries the 75 % cut, MaxVel does not)
# MissileCurrentFrame: the SERVER's frame counter starts at Range and counts down (D2MOO MISSILES_CreateMissileFromParams:
# SetCurrentFrame(range); MISSMODE: frame - 1 each update, dies at 0), but the attribute the client gives the HD effect
# counts UP, 0 -> Range (frames flown; lane calib5): Frost Nova's ring in timed game shots (no capture stall) sits on the
# stepped missile track (rms 0.7 units over 12 shots) and its Core quads' size, a function of CurrentFrame / Frames
# only, matches the viewer at the frame the radius matches only when counting up; the effect's own spawn stagger
# (MissileIndex % 4 at once, % 2 at ratio >= 0.066, the rest at >= 0.14) also needs a rising ratio.
MISSILE_FRAME_COUNTS_DOWN = False
# `overlay_at="lead"`: the caster overlays this long before the missiles' release (lane calib5's reading of calib5 t2 with
# a bow-class action frame). Lane r5-casttiming: the game starts them with the cast mode (overlay_at="start", the default);
# with the bench character's staff (SOSCSTF: action frame 5 at speed 416) that is 0.123 s before the release.
CAST_OVERLAY_LEAD_S = 0.08

try:                             # the vm lane's definition when it exists (docs/hdfx.md section 2)
    from .sim import SimContext  # type: ignore
except Exception:                # noqa: BLE001 - sim.py may not exist yet or fail to import: use the documented fields
    @dataclass
    class SimContext:            # type: ignore[no-redef]
        transform: np.ndarray = field(default_factory=lambda: np.eye(4))   # effect -> world
        velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
        target: np.ndarray | None = None
        bones: object = None                                               # callable(name) -> 4x4
        ground_z: float = 0.0
        emitter_shape: object = None
        time_scale: float = 1.0
        running: bool = True


# ---- small maths --------------------------------------------------------------------------------------------------------------

def _trs(td: dict | None) -> np.ndarray:
    from hdkit.view import preset
    return preset.trs(td)


def _vec(d, default=(0.0, 0.0, 0.0)):
    d = d or {}
    return tuple(float(d.get(k, v)) for k, v in zip("xyz", default))


def _rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    M = np.eye(4)
    M[:3, :3] = [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]
    return M


def _translate(p) -> np.ndarray:
    M = np.eye(4)
    M[:3, 3] = np.asarray(p, float)[:3]
    return M


def _yaw_of(direction) -> float:
    """World yaw (degrees, `camera.rot_y`) that turns +z to the horizontal direction (x, ., z); 0 when it has none."""
    d = np.asarray(direction, float)
    return math.degrees(math.atan2(d[0], d[2])) if abs(d[0]) + abs(d[2]) > 1e-9 else 0.0


def norm_key(name: str) -> str:
    """The key of D2R's HD name maps for a missiles.txt / overlay.txt name (knowledge/hd-graphics.md, HD json maps)."""
    return re.sub(r"(?<=[^_\d])(\d+)", r"_\1", name.lower().replace(" ", "_"))


# ---- tables (the mod's txt data, read from a folder the caller names) -----------------------------------------------------------

def read_table(data: bytes | str | Path) -> list[dict]:
    """A tab-separated D2 txt table -> rows as dicts keyed by the lower-case header; empty and `Expansion` rows dropped."""
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str) and ("\n" in data or "\t" in data):
        raw = data.encode("utf-8")
    else:
        raw = Path(data).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    lines = text.replace("\r", "").split("\n")
    if not lines or not lines[0].strip():
        return []
    head = [h.strip().lower() for h in lines[0].split("\t")]
    rows = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        cells = ln.split("\t")
        row = {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(head) if h}
        first = cells[0].strip()
        if first and first.lower() != "expansion":
            rows.append(row)
    return rows


class Tables:
    """Rows of the txt tables by name (`missiles`, `skills`, `overlay`, ...): `Tables.from_dir(excel_folder)` or
    `Tables({"missiles": bytes_or_path_or_rows, ...})`. The first column of a table is its key (case-insensitive,
    the first row of a name wins, as the game's own lookup)."""
    KEYS = {"missiles": "missile", "skills": "skill", "overlay": "overlay", "monstats": "id", "monstats2": "id",
            "states": "state"}

    def __init__(self, sources: dict):
        self._src = {k.lower(): v for k, v in sources.items()}
        self._rows: dict[str, list] = {}
        self._idx: dict[str, dict] = {}

    @classmethod
    def from_dir(cls, folder) -> "Tables":
        d = Path(folder)
        return cls({p.stem: p for p in d.glob("*.txt")} if d.is_dir() else {})

    def has(self, table: str) -> bool:
        return table.lower() in self._src

    def rows(self, table: str) -> list[dict]:
        t = table.lower()
        if t not in self._rows:
            s = self._src.get(t)
            self._rows[t] = [] if s is None else (list(s) if isinstance(s, list) else read_table(s))
        return self._rows[t]

    def row(self, table: str, name: str) -> dict | None:
        t = table.lower()
        if t not in self._idx:
            key = self.KEYS.get(t)
            idx: dict[str, dict] = {}
            for r in self.rows(t):
                k = (r.get(key) if key else next(iter(r.values()), "")) or ""
                idx.setdefault(k.lower(), r)
                if t == "skills" and r.get("*id", "") not in ("",):
                    idx.setdefault("#" + r["*id"], r)
            self._idx[t] = idx
        return self._idx[t].get(str(name).lower())


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except ValueError:
        return default


# ---- what the game binds ------------------------------------------------------------------------------------------------------

@dataclass
class Binding:
    """One entity of a unit / missile / overlay JSON that draws something."""
    kind: str                       # vfx | objecteffect | light | model | hitfx
    entity: str
    file: str = ""                  # effect (.particles) or model path; for a light the empty string
    bone: str | None = None         # EntityAttachmentDefinitionComponent.targetbone
    xf: np.ndarray = field(default_factory=lambda: np.eye(4))     # TransformDefinitionComponent as the game applies it
    position: tuple = (0.0, 0.0, 0.0)
    scale: tuple = (1.0, 1.0, 1.0)
    orientation: tuple = (0.0, 0.0, 0.0, 1.0)
    inherit_only_position: bool = False
    source_skill: str = ""          # UnitSourceSkillFilterComponent: draws only for this skill ("" = always)
    skill_default: bool = False     # a UnitSourceSkillFilterComponent with an empty sourceSkill: the definition's default,
                                    # left out when another entity names the skill (skill_pick; lane r4-firewall)
    release_point: bool = False     # TrackReleasePointDefinitionComponent: follows the unit that released it
    hard_kill: bool = False
    extra: dict = field(default_factory=dict)
    trigger: str = "entity"         # what starts it: entity (unit JSON, lives with the unit) | name (monster-folder overlay named
                                    # like the unit) | preload (unit preload list: loaded, not played) | timeline (animation
                                    # event) | overlay | missile | state (the definition was reached by name or column)
    mode: str = ""                  # trigger "timeline": the unit mode token (`a1`, `dt` ...) the event plays in, else the
                                    # timeline's own name lower-cased
    attach: bool | None = None      # timeline: attachToEntity (follows the bone), None when the trigger has no such flag
    time: float | None = None       # timeline: seconds into the mode's clip at which the effect starts

    def applies(self, skill: str | None = None, mode: str | None = None, preload: bool = False) -> bool:
        """Does it draw for this skill / unit mode? A timeline binding draws only for its own `mode` (asked for), a preload
        binding only when `preload`; the skill filter works as before."""
        if self.trigger == "timeline" and (mode is None or str(mode).lower() != self.mode.lower()):
            return False
        if self.trigger == "preload" and not preload:
            return False
        return not self.source_skill or (skill is not None and self.source_skill.lower() == skill.lower())

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "entity": self.entity, "file": self.file, "bone": self.bone,
             "position": list(self.position), "scale": list(self.scale), "orientation": list(self.orientation),
             "inherit_only_position": self.inherit_only_position, "source_skill": self.source_skill,
             "release_point": self.release_point, "hard_kill": self.hard_kill, "trigger": self.trigger}
        if self.mode:
            d["mode"] = self.mode
        if self.attach is not None:
            d["attach"] = self.attach
        if self.time is not None:
            d["time"] = self.time
        d.update(self.extra)
        return d


def skill_pick(bindings: list, skill: str | None) -> list:
    """The bindings the game plays for `skill`: an entity whose skill filter is empty (`skill_default`) is the default and
    is left out when another entity names the skill. READ from the game: in the Fire Wall capture (lane r3-integrate g1)
    the firewall missile's disc mesh draw has 8 instances and its cylinder draw 3; ground_fire_big.json's empty-filter
    entity (vfx_ground_fire_big, a cylinder in every copy) would add a cylinder per fire, its `Fire Wall` entity
    (vfx_ground_fire_firewall, a cylinder in a third of the copies) gives 8 and ~3 alone."""
    if not skill or not any(b.source_skill and b.source_skill.lower() == skill.lower() for b in bindings):
        return bindings
    return [b for b in bindings if not getattr(b, "skill_default", False)]


def _comps(ent: dict) -> dict:
    return {c.get("type"): c for c in ent.get("components", [])}


def bindings_of(defn: dict) -> list[Binding]:
    """Every drawing entity of a UnitDefinition / OverlayDefinition JSON, in file order."""
    out = []
    for ent in defn.get("entities", []):
        c = _comps(ent)
        td = c.get("TransformDefinitionComponent")
        pos, scl = _vec((td or {}).get("position")), _vec((td or {}).get("scale"), (1.0, 1.0, 1.0))
        ori = tuple(float((td or {}).get("orientation", {}).get(k, d)) for k, d in zip("xyzw", (0, 0, 0, 1)))
        common = dict(entity=ent.get("name", ""), bone=(c.get("EntityAttachmentDefinitionComponent") or {}).get("targetbone"),
                      xf=_trs(td), position=pos, scale=scl, orientation=ori,
                      inherit_only_position=bool((td or {}).get("inheritOnlyPosition", False)),
                      source_skill=(c.get("UnitSourceSkillFilterComponent") or {}).get("sourceSkill", "") or "",
                      skill_default="UnitSourceSkillFilterComponent" in c and not (
                          c.get("UnitSourceSkillFilterComponent") or {}).get("sourceSkill"),
                      release_point="TrackReleasePointDefinitionComponent" in c)
        v, oe, md, sl = (c.get("VfxDefinitionComponent"), c.get("ObjectEffectDefinitionComponent"),
                         c.get("ModelDefinitionComponent"), c.get("SpotLightDefinitionComponent"))
        if v:
            out.append(Binding("vfx", file=v.get("filename", ""), hard_kill=bool(v.get("hardKillOnDestroy")), **common))
        if oe:
            out.append(Binding("objecteffect", file=oe.get("filename", ""),
                               extra={"model": (md or {}).get("filename", ""),
                                      "warlock_weapon": bool(oe.get("isWarlockWeaponVfx"))}, **common))
        elif md:
            out.append(Binding("model", file=md.get("filename", ""), extra={"light_mask": md.get("lightMask")}, **common))
        if sl:
            out.append(Binding("light", extra={k: sl.get(k) for k in ("color", "power", "distance", "angle", "attenuation",
                                                                      "isLocalLight", "lightMask")}, **common))
    return out


def hit_effects(defn: dict) -> list[dict]:
    """The DamageFXComponent's hit effects: [{table, rule, file, hit_classes, victims, blood_types}], and its spawn bones."""
    out = []
    for ent in defn.get("entities", []):
        d = _comps(ent).get("DamageFXComponent")
        if not d:
            continue
        for tname in ("attackAttemptVfxOverrides", "attackSuccessVfxOverrides", "attackFailVfxOverrides"):
            t = d.get(tname) or {}
            for r in t.get("rules", []) if isinstance(t, dict) else []:
                out.append({"table": tname[len("attack"):-len("VfxOverrides")].lower(), "name": t.get("name", ""),
                            "file": r.get("vfxFile", ""), "hit_classes": r.get("acceptedHitClasses"),
                            "victims": r.get("acceptedVictims"), "blood_types": r.get("acceptedBloodTypes")})
            if isinstance(t, dict) and t.get("defaultVfxFile"):
                out.append({"table": tname[len("attack"):-len("VfxOverrides")].lower(), "name": t.get("name", ""),
                            "file": t["defaultVfxFile"], "default": True})
        for b in d.get("vfxSpawnBones") or []:
            out.append({"table": "spawn_bone", "bone": b if isinstance(b, str) else json.dumps(b)})
    return out


def _json(fs, logical: str) -> dict:
    return json.loads(fs.read(logical).decode("utf-8-sig"))


def _root(defn: dict) -> dict:
    """Root facts of a unit / missile JSON: scale (as scene.load_unit: y), rotationOverride, skeleton file."""
    r = {"scale": 1.0, "rotation_override": (0.0, 0.0, 0.0, 1.0), "skeleton": None, "state_machine": ""}
    for ent in defn.get("entities", []):
        c = _comps(ent)
        u = c.get("UnitRootComponent")
        if not u:
            continue
        ro = u.get("rotationOverride") or {}
        r["rotation_override"] = tuple(float(ro.get(k, d)) for k, d in zip("xyzw", (0, 0, 0, 1)))
        r["state_machine"] = u.get("state_machine_filename", "")
        r["skeleton"] = (c.get("SkeletonDefinitionComponent") or {}).get("filename")
        td = c.get("TransformDefinitionComponent")
        if td:
            r["scale"] = float((td.get("scale") or {}).get("y", 1.0))
        break
    return r


@dataclass
class UnitEffects:
    unit: str
    path: str
    root_scale: float
    rotation_override: tuple
    bindings: list
    hit: list
    skeleton: str | None = None
    mode_map: dict = field(default_factory=dict)      # timeline name (lower case) -> unit mode token
    overlay_height: dict | None = None                # monstats2 OverlayHeight -> overlay.txt HeightN (see overlay_height)
    notes: list = field(default_factory=list)

    def vfx(self, skill: str | None = None, mode: str | None = None, preload: bool = False) -> list:
        """The effects that draw for the unit: its entities and the name-bound overlay always; with `mode` also the timeline
        events of that mode (at `Binding.time` into its clip); with `preload` the overlays it only loads."""
        return skill_pick([b for b in self.bindings if b.kind in ("vfx", "objecteffect") and b.file
                           and b.applies(skill, mode, preload)], skill)

    def timeline(self, mode: str | None = None) -> list:
        """Timeline bindings (SpawnVFX / SpawnObjectEffect), those of unit mode `mode` when given, in clip-time order."""
        return sorted((b for b in self.bindings if b.trigger == "timeline" and (mode is None or b.mode.lower() == str(mode).lower())),
                      key=lambda b: (b.mode, b.time or 0.0))

    def as_dict(self) -> dict:
        return {"unit": self.unit, "path": self.path, "root_scale": self.root_scale,
                "rotation_override": list(self.rotation_override), "skeleton": self.skeleton,
                "bindings": [b.as_dict() for b in self.bindings], "hit_effects": self.hit,
                "overlay_height": self.overlay_height, "notes": self.notes}


def unit_path(fs, unit: str) -> str | None:
    """Logical path of a unit JSON from a name (`mummy1`) or a path; the monsters.json map and a folder walk are tried."""
    if unit.lower().endswith(".json"):
        return unit if fs.exists(unit) else None
    stem = unit.lower()
    for base in ("hd/character/enemy/", "hd/character/npc/", "hd/character/player/"):
        if fs.exists("%s%s.json" % (base, stem)):
            return "%s%s.json" % (base, stem)
    try:
        mp = _json(fs, "hd/character/monsters.json")
        alias = next((v for k, v in mp.items() if k.lower() == stem and isinstance(v, str)), None)
        if alias and alias != stem:
            return unit_path(fs, alias)
    except (OSError, ValueError):
        pass
    if hasattr(fs, "walk"):
        for h in fs.walk("hd/character", "/%s.json" % stem):
            return h.logical
    return None


def resolve_particle(fs, file: str) -> str | None:
    """Logical path of a `.particles` file named by a JSON or timeline, matched case-insensitively (the JSON says
    `ambientFire`, the extracted file is `ambientfire`; `Layered` already ignores case, a plain `exists` fs may not).
    None when no root has it."""
    if not file:
        return None
    from hdkit.view import vfs
    p = vfs.normalise(file)
    if fs.exists(p):
        return p
    if hasattr(fs, "walk"):
        d, _, name = p.rpartition("/")
        for h in fs.walk(d, "/" + name):
            if h.logical.lower() == p:
                return h.logical
    return None


# ---- unit modes and animation timelines -----------------------------------------------------------------------------------------

# state name of a unit state machine (without the `State_` prefix, lower case) -> mode token, for units whose state machine
# cannot be read; `mode_map_of` overrides it from the real one (same ids as view.player.MODE_TOKENS)
DEFAULT_MODE_MAP = {"death": "dt", "neutral": "nu", "walk": "wl", "gethit": "gh", "attack1": "a1", "attack2": "a2", "block": "bl",
                    "cast": "sc", "skill1": "s1", "skill2": "s2", "skill3": "s3", "skill4": "s4", "dead": "dd",
                    "knockback": "kb", "sequence": "sq", "run": "rn"}
_MODE_TOKENS = {0: "dt", 1: "nu", 2: "wl", 3: "gh", 4: "a1", 5: "a2", 6: "bl", 7: "sc", 8: "s1", 9: "s2", 10: "s3", 11: "s4",
                12: "dd", 13: "kb", 14: "sq", 15: "rn"}


def mode_map_of(state_machine: dict | None = None) -> dict:
    """Timeline name (lower case) -> mode token: the defaults, then the state machine's `states` (`State_Attack1` modeId 4 ->
    `attack1` -> `a1`; the timeline of a state carries the state's name without `State_`)."""
    m = dict(DEFAULT_MODE_MAP)
    for st in (state_machine or {}).get("states", []):
        tok = _MODE_TOKENS.get(st.get("modeId"))
        name = str(st.get("name", "")).lower()
        if tok and name:
            m[name[6:] if name.startswith("state_") else name] = tok
    return m


def timeline_path(fs, unit_json: str, skeleton: str | None = None) -> str | None:
    """`<unit folder>/animation/combined.timelines` by convention (no JSON names it): the folder is the unit JSON's stem, or the
    skeleton file's folder two levels up when the stem has none."""
    cands = [unit_json[:-5] + "/animation/combined.timelines"]
    if skeleton:
        cands.append(skeleton.replace("\\", "/").lower().rsplit("/skeleton/", 1)[0] + "/animation/combined.timelines")
    return next((c for c in cands if fs.exists(c)), None)


def _euler_xf(offset: dict | None, rotation: dict | None) -> np.ndarray:
    """translationOffset and rotationOffset (euler degrees) -> 4x4. The rotation order (z, then x, then y) is an assumption: 3 of
    476 corpus events have a rotation."""
    M = _translate(_vec(offset))
    r = _vec(rotation)
    if any(r):
        cx, sx, cz, sz = math.cos(math.radians(r[0])), math.sin(math.radians(r[0])), math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
        Rx = np.array([[1, 0, 0, 0], [0, cx, -sx, 0], [0, sx, cx, 0], [0, 0, 0, 1.0]])
        Rz = np.array([[cz, -sz, 0, 0], [sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])
        M = M @ _rot_y(r[1]) @ Rx @ Rz
    return M


def timeline_bindings(fs, unit_path_: str, mode_map: dict | None = None, skeleton: str | None = None) -> list[Binding]:
    """The SpawnVFX / SpawnObjectEffect / SpawnVFXConnected events of a unit's `animation/combined.timelines` as Bindings
    (trigger `timeline`; a SpawnVFXConnected has `bone` = sourceBoneName and `extra["target_bone"]` = targetBoneName).
    `mode` = `mode_map[timeline name]` (see `mode_map_of`) else the lower-cased timeline name; `time` = the event's timestamp
    in the clip (seconds); `bone` = `boneName` (SpawnVFX) or `unitPart` (SpawnObjectEffect: `extra["unit_part"]`); `attach` =
    `attachToEntity`; `xf` = the event's translation / rotation offset. Extras: duration, orientation_bone, attach_to_dead_body,
    hard_kill_on_timeout, timeline. Empty list when the unit has no timelines file."""
    path = timeline_path(fs, unit_path_, skeleton)
    if path is None:
        return []
    try:
        tl = _json(fs, path).get("timelines", {})
    except (OSError, ValueError):
        return []
    mm = {k.lower(): v for k, v in (mode_map if mode_map is not None else DEFAULT_MODE_MAP).items()}
    out = []
    for tname, t in tl.items():
        for ev in t.get("animationEvents", []):
            if ev.get("type") not in ("SpawnVFX", "SpawnObjectEffect", "SpawnVFXConnected"):
                continue
            pr = {p.get("parameterName"): p.get("value") for p in ev.get("eventParams", [])}
            fx = ev["type"] != "SpawnObjectEffect"
            conn = ev["type"] == "SpawnVFXConnected"       # a beam between two bones: sourceBoneName -> targetBoneName
            xf_ = _euler_xf(pr.get("translationOffset"), pr.get("rotationOffset"))
            out.append(Binding("vfx" if fx else "objecteffect", entity="%s:%s" % (tname, ev.get("name", ev["type"])),
                               file=pr.get("filename", "") or "", bone=(pr.get("sourceBoneName") if conn else pr.get("boneName") if fx else pr.get("unitPart")) or None,
                               xf=xf_, position=tuple(float(v) for v in xf_[:3, 3]), hard_kill=bool(pr.get("hardKillOnTimeout")), trigger="timeline",
                               mode=mm.get(tname.lower(), tname.lower()), attach=bool(pr.get("attachToEntity")) if fx else None,
                               time=float(ev.get("eventTimestamp", 0.0)),
                               extra={"timeline": tname, "event": ev["type"], "duration": float(ev.get("duration", 0.0)),
                                      "unit_part": not fx, "orientation_bone": pr.get("orientationBoneName") or "",
                                      "attach_to_dead_body": bool(pr.get("attachToDeadBody", False)),
                                      **({"target_bone": pr.get("targetBoneName") or ""} if conn else {})}))
    return out


# ---- overlays that belong to a unit -----------------------------------------------------------------------------------------------

def overlay_height(tables: Tables | None, monster: str | None, overlay_row: dict | None = None, index: int | None = None) -> dict | None:
    """monstats2 `OverlayHeight` (1..4) of a monster -> the `Height1..4` column of an overlay.txt row, in pixels of the classic
    overlay (0 for every overlay shipped so far). UNVERIFIED: the pairing is by column names only, nothing seen in game.
    `index` overrides the monster lookup. None without a monstats2 row or an index."""
    if index is None and tables is not None and monster and tables.has("monstats2"):
        r = tables.row("monstats2", monster)
        index = int(_num(r.get("overlayheight"), 0)) if r else None
    if not index:
        return None
    out = {"index": index, "verified": False, "px": None}
    if overlay_row:
        out["px"] = _num(overlay_row.get("height%d" % index), 0.0)
    return out


def _tagged(binds: list, trigger: str, **extra) -> list:
    for b in binds:
        b.trigger = trigger
        b.extra.update(extra)
    return binds


def _unit_overlays(fs, d: dict, path: str, unit: str, tables: Tables | None, notes: list) -> tuple[list, dict | None]:
    """Overlay bindings of a unit: (1) the monster-folder overlay named like the unit (trigger `name`: INFERRED, no data
    names it; the key is tried for the argument, the JSON's `name` and the file stem, so `ubermephisto` -> `mephisto` unit
    gets Mephisto's smoke as the in-game calibration showed), (2) the overlays of DefinitionPreloadComponent.overlays that
    are not already played that way (trigger `preload`: loaded, not necessarily played)."""
    out, seen, height = [], set(), None
    if "/character/player/" not in path:
        names = []
        for n in (unit if not unit.lower().endswith(".json") else "", d.get("name", ""), Path(path).stem):
            for k in (n.lower(), norm_key(n)) if n else ():
                if k and k not in names:
                    names.append(k)
        for k in names:
            p = "hd/overlays/monster/%s.json" % k
            if fs.exists(p) and p not in seen:
                seen.add(p)
                row = tables.row("overlay", k) if tables is not None and tables.has("overlay") else None
                height = height or overlay_height(tables, unit, row)
                out += _tagged(bindings_of(_json(fs, p)), "name", overlay=k, overlay_path=p)
    for ent in d.get("entities", []):
        for e in (_comps(ent).get("DefinitionPreloadComponent") or {}).get("overlays", []):
            from hdkit.view import vfs
            p = vfs.normalise(e.get("filename", ""))
            if not p or p in seen or not fs.exists(p):
                continue
            seen.add(p)
            out += _tagged(bindings_of(_json(fs, p)), "preload", overlay=Path(p).stem, overlay_path=p)
    if not out:
        notes.append("no monster-folder overlay named like the unit and no preloaded overlay")
    return out, height


# ---- bindings the first survey missed (lane bind2, docs/hdfx.md section 6) -----------------------------------------------------
# `dependencies.particles[]` / `dependencies.objecteffects[]` of every JSON are preload manifests: they say what to load, never
# what plays, so they are not read here.

def _paths(node, path: str):
    """Values under a key path `a/b[]/c` (`[]` after a key walks that list; missing keys give nothing)."""
    if not path:
        yield node
        return
    head, _, rest = path.partition("/")
    each = head.endswith("[]")
    key = head[:-2] if each else head
    if not isinstance(node, dict) or key not in node:
        return
    v = node[key]
    for x in (v if isinstance(v, list) else []) if each else [v]:
        yield from _paths(x, rest)


def _fx(kind: str, trigger: str, file, entity: str = "", **extra) -> Binding | None:
    """A Binding for a plain `.particles` string of a table or component (None when it is empty)."""
    return Binding(kind, entity=entity, file=file, trigger=trigger, extra=extra) if isinstance(file, str) and file else None


def _retag_objects(binds: list) -> list:
    """`ObjectEffectDefinitionComponent.filename` entities (208 particles, e.g. ancientbarb1 `entity_torso` ->
    `vfx_ancientbarb1_objfx`): the unit's per-model effect, trigger `object` (it plays while its model does)."""
    for b in binds:
        if b.kind == "objecteffect":
            b.trigger = "object"
    return binds


def object_effects(fs, unit: str) -> list[Binding]:
    """The ObjectEffectDefinitionComponent bindings of a unit JSON (trigger `object`), as `effects_of_unit` lists them."""
    path = unit_path(fs, unit)
    if path is None:
        raise FileNotFoundError("unit %s: no unit JSON found under hd/character" % unit)
    return [b for b in _retag_objects(bindings_of(_json(fs, path))) if b.kind == "objecteffect" and b.file]


def _vfx_table(t, trigger: str, entity: str = "", **extra) -> list[Binding]:
    """An `AttackVFXTable` (`rules[].vfxFile` + `defaultVfxFile`) as `hitfx` Bindings; entity = the owner (else the rule's name);
    extra: table name, accepted masks, `default` for the table's default file."""
    if not isinstance(t, dict):
        return []
    out = [_fx("hitfx", trigger, r.get("vfxFile"), entity or r.get("name", ""), table=t.get("name", ""), rule=r.get("name", ""),
               hit_classes=r.get("acceptedHitClasses"), victims=r.get("acceptedVictims"), blood_types=r.get("acceptedBloodTypes"), **extra)
           for r in t.get("rules") or []]
    out.append(_fx("hitfx", trigger, t.get("defaultVfxFile"), entity or t.get("name", ""), table=t.get("name", ""), default=True, **extra))
    return [b for b in out if b]


def attack_success_bindings(defn: dict) -> list[Binding]:
    """`attackSuccessVfxOverrides` rules and default of every component of a unit JSON (the DamageFXComponent; 8 particles the
    census missed), trigger `attack_success`, kind `hitfx` (not drawn by `UnitEffects.vfx()`: a hit effect plays on a hit)."""
    out = []
    for ent in defn.get("entities", []):
        for c in ent.get("components", []):
            out += _vfx_table(c.get("attackSuccessVfxOverrides"), "attack_success", entity=ent.get("name", ""))
    return out


def attack_success_effects(fs, unit: str | None = None) -> list[Binding]:
    """Success hit effects of a unit (`attackSuccessVfxOverrides`), or with `unit=None` the global `attackSuccessVfxBlood` /
    `attackSuccessVfxNoBlood` tables of `hd/global/excel/attackfxglobals.json`."""
    if unit is None:
        return [b for b in global_tables_effects(fs) if b.extra.get("table_kind") == "attack_success"]
    path = unit_path(fs, unit)
    if path is None:
        raise FileNotFoundError("unit %s: no unit JSON found under hd/character" % unit)
    return attack_success_bindings(_json(fs, path))


def screenspace_effects(fs, env: str) -> list[Binding]:
    """`screenspaceVfx` of the components of an environment vis JSON (`hd/env/vis/<env>.json`, 20 particles): the effect drawn
    over the screen while the vis is active (trigger `screenspace`, kind `vfx`, no bone, identity transform)."""
    path = env if env.lower().endswith(".json") else "hd/env/vis/%s.json" % env
    d = _json(fs, path)
    out = []
    for ent in d.get("entities", []):
        for c in ent.get("components", []):
            b = _fx("vfx", "screenspace", c.get("screenspaceVfx"), ent.get("name", ""), component=c.get("type", ""), env=path)
            out += [b] if b else []
    return out


# (file, key path, trigger kind): the named particles of global tables. A weather map names rain, snow, ripples (splash) and
# bubbles per water kind; footprints one per footprint id; the attack tables are handled by `_vfx_table`.
WEATHER_KEYS = ("rain", "snow", "splash1", "splash2", "splash3", "splash4", "bubble1", "bubble2", "bubble3", "bubble4")
GLOBAL_TABLES = (
    [("hd/global/footprintdecals.json", "parameters[]/vfxFile", "footprint"),
     ("hd/global/excel/controllersettings.json", "config/freeTargetVFXFilename", "ui"),
     ("hd/global/excel/gameplay_translation_settings.json", "goldPickupVFXFilename", "ui")]
    + [("hd/env/weather/weather_effects.json", k, "weather") for k in WEATHER_KEYS])
ATTACK_GLOBALS = "hd/global/excel/attackfxglobals.json"
ATTACK_KEYS = tuple("attack%sVfx%s" % (a, b) for a in ("Attempt", "Success", "Fail") for b in ("Blood", "NoBlood"))
VFX_ONLY = "hd/objects/vfx_only"
VFX_ONLY_UNITS = ("town_portal",)     # the vfx_only units that keep their effects in a VfxVariantComponent; a CASC root cannot be listed


def global_tables_effects(fs) -> list[Binding]:
    """Effects named by global tables (trigger `global`, `extra["table"]` = file, `extra["key"]` = key path, `extra["table_kind"]`):
    footprint decals, weather (rain, snow, splash, bubbles), the attack hit tables `attack{Attempt,Success,Fail}Vfx{Blood,NoBlood}`
    (a language dict for the fail ones: `extra["lang"]`), the free-target reticle, the gold pickup and the `hd/objects/vfx_only`
    units' `VfxDefinitionComponent` / `VfxVariantComponent` (`extra["variant"]`)."""
    out: list[Binding] = []

    def load(path):
        try:
            return _json(fs, path)
        except (OSError, ValueError, KeyError):
            return None
    for path, kp, kind in GLOBAL_TABLES:
        d = load(path)
        for i, v in enumerate(_paths(d, kp) if d else ()):
            b = _fx("vfx", "global", v, "%s:%s" % (Path(path).stem, kp), table=path, key=kp, table_kind=kind, index=i)
            out += [b] if b else []
    g = load(ATTACK_GLOBALS) or {}
    for k in ATTACK_KEYS:
        t = g.get(k)
        tables = [(None, t)] if isinstance(t, dict) and ("rules" in t or "defaultVfxFile" in t) else \
            sorted((l, x) for l, x in (t or {}).items() if isinstance(x, dict)) if isinstance(t, dict) else []
        for lang, x in tables:
            tk = "attack_success" if "Success" in k else "attack"
            out += _vfx_table(x, "global", k, key=k + ("/" + lang if lang else ""), source=ATTACK_GLOBALS, table_kind=tk,
                              **({"lang": lang} if lang else {}))
    units = {h.logical for h in fs.walk(VFX_ONLY, ".json")} if hasattr(fs, "walk") else set()
    for logical in sorted(units | {"%s/%s.json" % (VFX_ONLY, n) for n in VFX_ONLY_UNITS}):
        d = load(logical) or {}
        for ent in d.get("entities", []):
            for c in ent.get("components", []):
                vs = [c] if c.get("type") == "VfxDefinitionComponent" else [v for v in c.get("vfx", []) if isinstance(v, dict)] \
                    if c.get("type") == "VfxVariantComponent" and isinstance(c.get("vfx"), list) else []
                for i, v in enumerate(vs):
                    b = _fx("vfx", "global", v.get("filename"), "%s:%s" % (Path(logical).stem, ent.get("name", "")), table=logical,
                            key="vfx_only", table_kind="vfx_only", variant=i if c.get("type") == "VfxVariantComponent" else None)
                    out += [b] if b else []
    return out


def effects_of_unit(fs, unit: str, tables: Tables | None = None, mode_map: dict | None = None) -> UnitEffects:
    """Everything bound to a unit: its entities (trigger `entity`; the ObjectEffect ones `object`), its success hit effects
    (`attack_success`, kind `hitfx`), the overlay named like it (`name`) and its preload list
    (`preload`), and the animation timeline events (`timeline`, played per mode: `UnitEffects.vfx(mode=)`). `tables`
    (optional) adds the overlay height (`overlay_height`); `mode_map` overrides the state machine's timeline-name -> mode map."""
    path = unit_path(fs, unit)
    if path is None:
        raise FileNotFoundError("unit %s: no unit JSON found under hd/character" % unit)
    d = _json(fs, path)
    r = _root(d)
    notes: list = []
    binds = _retag_objects(bindings_of(d)) + attack_success_bindings(d)
    ov, height = _unit_overlays(fs, d, path, unit, tables, notes)
    if mode_map is None:
        sm = None
        if r["state_machine"]:
            try:
                sm = _json(fs, r["state_machine"])
            except (OSError, ValueError):
                notes.append("state machine %s unreadable: default timeline -> mode map" % r["state_machine"])
        mode_map = mode_map_of(sm)
    return UnitEffects(d.get("name", unit), path, r["scale"], r["rotation_override"],
                       binds + ov + timeline_bindings(fs, path, mode_map, r["skeleton"]), hit_effects(d), r["skeleton"],
                       mode_map, height, notes)


@dataclass
class MissileEffects:
    name: str
    key: str
    model: str | None
    path: str | None
    bindings: list
    row: dict = field(default_factory=dict)         # the missiles.txt facts (empty without tables)
    rotation_override: tuple = (0.0, 0.0, 0.0, 1.0)
    children: list = field(default_factory=list)    # [(column, missile name)] it makes on the client side
    notes: list = field(default_factory=list)

    def vfx(self, skill: str | None = None) -> list:
        return skill_pick([b for b in self.bindings if b.kind == "vfx" and b.file and b.applies(skill)], skill)

    def as_dict(self) -> dict:
        return {"name": self.name, "key": self.key, "model": self.model, "path": self.path,
                "bindings": [b.as_dict() for b in self.bindings], "row": self.row, "children": self.children,
                "rotation_override": list(self.rotation_override), "notes": self.notes}


MISSILE_CHILD_COLS = ("explosionmissile", "cltsubmissile1", "cltsubmissile2", "cltsubmissile3", "clthitsubmissile1",
                      "clthitsubmissile2", "clthitsubmissile3", "clthitsubmissile4")


def missile_facts(row: dict) -> dict:
    """The motion and look columns of a missiles.txt row, with the speed in world units per second."""
    vel, mx, acc = _num(row.get("vel")), _num(row.get("maxvel")), _num(row.get("accel"))
    return {"vel": vel, "maxvel": mx, "accel": acc, "range": _num(row.get("range")),
            "levrange": _num(row.get("levrange")), "range_expr": str(row.get("range", "") or "").strip(),
            "speed_units_s": vel * VEL_UNITS_PER_S, "accel_units_s2": acc * ACCEL_UNITS_PER_S2,
            "max_speed_units_s": mx * VEL_UNITS_PER_S * VEL_MAX_PER_VEL if mx else None,
            "light": _num(row.get("light")), "rgb": [_num(row.get(c)) for c in ("red", "green", "blue")],
            "offset_px": [_num(row.get(c)) for c in ("xoffset", "yoffset", "zoffset")],
            "trans": row.get("trans", ""), "celfile": row.get("celfile", ""), "loopanim": row.get("loopanim", ""),
            "client_do_func": row.get("pcltdofunc", ""), "client_hit_func": row.get("pclthitfunc", ""),
            "server_do_func": int(_num(row.get("psrvdofunc"))), "server_hit_func": int(_num(row.get("psrvhitfunc"))),
            "submissile": row.get("submissile1"), "hit_submissile": row.get("hitsubmissile1"),
            "hit_radius": int(_num(row.get("shitpar1"))), "hit_calc": row.get("shitcalc1"),
            "vellev": _num(row.get("vellev")),
            "radius": _num(row.get("radius")), "size": _num(row.get("size"))}


_NAME_MAPS: dict = {}


def _name_map(fs, logical: str) -> dict:
    k = (id(fs), logical)
    if k not in _NAME_MAPS or _NAME_MAPS[k][0] is not fs:
        try:
            m = {str(a).lower(): b for a, b in _json(fs, logical).items() if isinstance(b, str)}
        except (OSError, ValueError):
            m = {}
        _NAME_MAPS[k] = (fs, m)
    return _NAME_MAPS[k][1]


def effects_of_missile(fs, name: str, tables: Tables | None = None, depth: int = 0) -> MissileEffects:
    key = norm_key(name)
    row = tables.row("missiles", name) if tables is not None and tables.has("missiles") else None
    model = _name_map(fs, "hd/missiles/missiles.json").get(key)
    notes = []
    if model is None and fs.exists("hd/missiles/%s.json" % key):
        model = key
    path = "hd/missiles/%s.json" % model if model else None
    if path is None or not fs.exists(path):
        notes.append("no HD definition (%s)" % ("not in missiles.json" if model is None else "%s missing" % path))
        path = None if path is None or not fs.exists(path) else path
    binds, ro = [], (0.0, 0.0, 0.0, 1.0)
    if path:
        d = _json(fs, path)
        binds, ro = _tagged(bindings_of(d), "missile"), _root(d)["rotation_override"]
    kids = []
    if row:
        kids = [(c, row[c]) for c in MISSILE_CHILD_COLS if row.get(c)]
    return MissileEffects(name, key, model, path, binds, missile_facts(row) if row else {}, ro, kids, notes)


@dataclass
class OverlayEffects:
    name: str
    key: str
    folder: str | None
    path: str | None
    bindings: list
    row: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    height: dict | None = None      # {"index", "px", "verified": False}: monstats2 OverlayHeight -> overlay.txt HeightN (unverified)

    def vfx(self, skill: str | None = None) -> list:
        return skill_pick([b for b in self.bindings if b.kind == "vfx" and b.file and b.applies(skill)], skill)

    def as_dict(self) -> dict:
        return {"name": self.name, "key": self.key, "folder": self.folder, "path": self.path,
                "bindings": [b.as_dict() for b in self.bindings], "row": self.row, "notes": self.notes, "height": self.height}


def effects_of_overlay(fs, name: str, tables: Tables | None = None, monster: str | None = None,
                       height_index: int | None = None) -> OverlayEffects:
    """overlay.txt `Character` is the HD folder (`all` is 1.13c's, D2R wants `common`).
    `monster` (a monstats2 Id) or `height_index` (1..4) fills `.height`: that monster's `OverlayHeight` picks the row's
    `Height1..4` (unverified pairing, see `overlay_height`)."""
    key = norm_key(name)
    row = tables.row("overlay", name) if tables is not None and tables.has("overlay") else None
    folders = []
    if row and row.get("character"):
        c = row["character"].lower()
        folders.append("common" if c == "all" else c)
    folders += ["common", "monster"]
    path = folder = None
    for f in folders:
        p = "hd/overlays/%s/%s.json" % (f, key)
        if fs.exists(p):
            path, folder = p, f
            break
    notes = []
    if path is None and hasattr(fs, "walk"):
        hits = fs.walk("hd/overlays", "/%s.json" % key)
        if hits:
            path = hits[0].logical
            folder = path.split("/")[2]
            notes.append("found in folder %s, not the row's %s" % (folder, folders[0]))
    if path is None:
        notes.append("no HD overlay definition")
        return OverlayEffects(name, key, None, None, [], dict(row or {}), notes, overlay_height(tables, monster, row, height_index))
    return OverlayEffects(name, key, folder, path, _tagged(bindings_of(_json(fs, path)), "overlay"),
                          {k: v for k, v in (row or {}).items()
                           if k in ("filename", "character", "predraw", "trans", "xoffset", "yoffset", "animrate", "frames")}, notes,
                          overlay_height(tables, monster, row, height_index))


SKILL_MISSILE_COLS = ("cltmissile", "cltmissilea", "cltmissileb", "cltmissilec", "cltmissiled", "srvmissile",
                      "srvmissilea", "srvmissileb", "srvmissilec")
SKILL_OVERLAY_COLS = ("castoverlay", "cltoverlaya", "cltoverlayb", "srvoverlay", "tgtoverlay", "prgoverlay", "sumoverlay")


@dataclass
class SkillRef:
    column: str
    name: str
    kind: str                     # missile | overlay
    effects: object = None        # MissileEffects | OverlayEffects


@dataclass
class SkillEffects:
    name: str
    anim: str
    monanim: str
    refs: list
    notes: list = field(default_factory=list)
    row: dict | None = None       # the skills.txt row (lower-case keys), for cast(): calc1, param*, srvdofunc

    fire_missiles: dict[str, MissileEffects] = field(default_factory=dict)
    run_velocity: float = 9.0     # charstats.txt run velocity of the caster used for a Blaze preview

    @property
    def mode(self) -> str:
        """The unit mode token (`a1`, `sc`, `s1` ...) of skills.txt `anim`; `th` (throw) plays as `a1`, `kk` (kick) as `a2`."""
        a = (self.anim or "").lower()
        return {"th": "a1", "kk": "a2", "": "sc"}.get(a, a)

    def as_dict(self) -> dict:
        return {"name": self.name, "anim": self.anim, "mode": self.mode, "monanim": self.monanim, "notes": self.notes,
                "refs": [{"column": r.column, "name": r.name, "kind": r.kind,
                          "effects": r.effects.as_dict() if r.effects else None} for r in self.refs]}


def effects_of_skill(fs, name: str, tables: Tables) -> SkillEffects:
    row = tables.row("skills", name)
    if row is None:
        raise KeyError("skill %s is not in skills.txt" % name)
    refs = []
    for kind, cols, fn in (("missile", SKILL_MISSILE_COLS, effects_of_missile), ("overlay", SKILL_OVERLAY_COLS, effects_of_overlay)):
        for c in cols:
            v = row.get(c, "")
            if not v:
                continue
            same = next((r for r in refs if r.kind == kind and r.name.lower() == v.lower()), None)
            if same:                      # cltmissile and srvmissile naming the same row: one entry, both columns
                same.column += "," + c
            else:
                refs.append(SkillRef(c, v, kind, fn(fs, v, tables)))
    notes = [] if refs else ["no missile or overlay column set"]
    from . import firemakers
    sk = SkillEffects(row.get("skill", name), row.get("anim", ""), row.get("monanim", ""), refs, notes, row)
    firemakers.resolve(sk, fs, tables)
    return sk


# states.txt: which column plays when (from the column names; the engine's own timing is not read)
STATE_OVERLAY_COLS = {"overlay1": "while active", "overlay2": "while active", "overlay3": "while active", "overlay4": "while active",
                      "castoverlay": "on cast / apply", "pgsvoverlay": "on apply (pgsv)", "removerlay": "on remove"}


@dataclass
class StateEffects:
    name: str
    refs: list                    # [SkillRef(column, overlay name, "overlay", OverlayEffects)]
    notes: list = field(default_factory=list)

    @property
    def bindings(self) -> list:
        return [b for r in self.refs if r.effects is not None for b in r.effects.bindings]

    def vfx(self, skill: str | None = None) -> list:
        return skill_pick([b for b in self.bindings if b.kind == "vfx" and b.file and b.applies(skill)], skill)

    def as_dict(self) -> dict:
        return {"name": self.name, "notes": self.notes,
                "refs": [{"column": r.column, "when": STATE_OVERLAY_COLS.get(r.column, ""), "name": r.name,
                          "effects": r.effects.as_dict() if r.effects else None} for r in self.refs]}


def effects_of_state(fs, name: str, tables: Tables) -> StateEffects:
    """states.txt row -> the overlays of `overlay1..4`, `castoverlay`, `pgsvoverlay`, `removerlay` (each resolved as an overlay;
    `hd/states/` holds no files, states live only in the txt). Their bindings carry trigger `state` and extra `column`."""
    row = tables.row("states", name)
    if row is None:
        raise KeyError("state %s is not in states.txt" % name)
    refs = []
    for c in STATE_OVERLAY_COLS:
        v = row.get(c, "")
        if v:
            o = effects_of_overlay(fs, v, tables)
            _tagged(o.bindings, "state", column=c, state=row.get("state", name))
            refs.append(SkillRef(c, v, "overlay", o))
    return StateEffects(row.get("state", name), refs, [] if refs else ["no overlay column set"])


@dataclass
class MonsterEffects:
    id: str
    unit: UnitEffects | None
    skills: list                  # [{"slot", "skill", "mode", "level", "effects": SkillEffects | None, "note"}]
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "unit": self.unit.as_dict() if self.unit else None, "notes": self.notes,
                "skills": [dict(s, effects=s["effects"].as_dict() if s["effects"] else None) for s in self.skills]}


def effects_of_monster(fs, monster: str, tables: Tables, unit: bool = True) -> MonsterEffects:
    """monstats.txt row (`Id`) -> its unit (`effects_of_unit`, with tables) and `Skill1..8` -> `effects_of_skill` (the skill's
    missiles and overlays; `Sk<n>mode` is the unit mode the monster casts it in)."""
    row = tables.row("monstats", monster)
    if row is None:
        raise KeyError("monster %s is not in monstats.txt" % monster)
    notes, skills, ufx = [], [], None
    if unit:
        try:
            ufx = effects_of_unit(fs, row.get("id", monster), tables)
        except FileNotFoundError as e:
            notes.append(str(e))
    for i in range(1, 9):
        sk = row.get("skill%d" % i, "")
        if not sk:
            continue
        ent = {"slot": i, "skill": sk, "mode": row.get("sk%dmode" % i, ""), "level": row.get("sk%dlvl" % i, ""),
               "effects": None, "note": ""}
        try:
            ent["effects"] = effects_of_skill(fs, sk, tables)
        except KeyError:
            ent["note"] = "not in skills.txt"
        skills.append(ent)
    return MonsterEffects(row.get("id", monster), ufx, skills, notes)


# ---- layers: what a driver places -----------------------------------------------------------------------------------------------

@dataclass
class Layer:
    effect: object                    # a path, an Effect, anything the Sim takes
    xf: np.ndarray = field(default_factory=lambda: np.eye(4))
    bone: str | None = None
    inherit_only_position: bool = False
    label: str = ""


def layers_of(x, skill: str | None = None) -> list[Layer]:
    """Layers of an effect argument: a Binding, a MissileEffects/OverlayEffects/UnitEffects (their vfx bindings for `skill`),
    a list of those, or an opaque effect (path or object) with an identity transform."""
    if isinstance(x, Layer):
        return [x]
    if isinstance(x, Binding):
        return [Layer(x.file, x.xf, x.bone, x.inherit_only_position, x.entity)]
    if hasattr(x, "vfx") and hasattr(x, "bindings"):
        return [l for b in x.vfx(skill) for l in layers_of(b)]
    if isinstance(x, (list, tuple)) and not (len(x) in (3, 4) and all(isinstance(v, (int, float)) for v in x)):
        return [l for y in x for l in layers_of(y, skill)]
    return [Layer(x, label=str(x) if isinstance(x, str) else type(x).__name__)]


# ---- drivers ------------------------------------------------------------------------------------------------------------------

@dataclass
class CtxFrame:
    t: float                          # driver time
    age: float                        # time since the driver started (effect clock)
    contexts: list                    # one SimContext per layer
    events: list                      # [(t, name, payload)] that fall in (previous t, t]
    layers: list

    @property
    def ctx(self):
        return self.contexts[0] if self.contexts else None


class Driver:
    """Base: `transform_at(t)` (the driver's frame -> world), events, layers. Velocity is a central difference unless the
    subclass knows it."""
    kind = "driver"

    def __init__(self, layers, start: float = 0.0, duration: float = 1.0, events=None, name: str = ""):
        self.layers = layers
        self.start, self.duration, self.name = float(start), float(duration), name
        self.events: list = sorted(events or [], key=lambda e: e[0])
        self.children: list = []          # spawned drivers: (t, Driver); `drive()` plays them from their start
        self.stop_t: float | None = None  # driver time the game stops the effect (SimContext.running False from it)

    # subclass hooks
    def transform_at(self, t: float) -> np.ndarray:
        return np.eye(4)

    def velocity_at(self, t: float) -> np.ndarray:
        h = 0.02
        a, b = max(t - h, self.start), min(t + h, self.start + self.duration)
        if b - a < 1e-9:
            return np.zeros(3)
        return (self.transform_at(b)[:3, 3] - self.transform_at(a)[:3, 3]) / (b - a)

    def target_at(self, t: float):
        return None

    def bones_at(self, t: float):
        M = self.transform_at(t)
        return lambda name=None: M

    def layer_transform(self, t: float, layer: Layer) -> np.ndarray:
        M = self.transform_at(t)
        return M @ layer.xf

    def context(self, t: float, i: int = 0) -> SimContext:
        lay = self.layers[i]
        return SimContext(transform=self.layer_transform(t, lay), velocity=self.velocity_at(t), target=self.target_at(t),
                          bones=self.bones_at(t), ground_z=0.0, emitter_shape=None, time_scale=1.0,
                          running=self.running_at(t),
                          **({"collision": self.collision} if getattr(self, "collision", None) is not None else {}))

    def running_at(self, t: float) -> bool:
        """effect.isRunning at driver time `t`: until `stop_t` (None: never stopped)."""
        return self.stop_t is None or t < self.stop_t - 1e-9

    def frame(self, t: float, prev: float | None = None) -> CtxFrame:
        lo = -math.inf if prev is None else prev
        ev = [e for e in self.events if lo < e[0] <= t + 1e-9]
        return CtxFrame(t, t - self.start, [self.context(t, i) for i in range(len(self.layers))], ev, self.layers)

    def frames(self, fps: float = FPS, duration: float | None = None):
        n = int(round((self.duration if duration is None else duration) * fps))
        prev = None
        for i in range(n + 1):
            t = self.start + i / fps
            yield self.frame(t, prev)
            prev = t

    def event_times(self) -> dict:
        out: dict = {}
        for t, name, _p in self.events:
            out.setdefault(name, t)
        return out


class StaticDriver(Driver):
    """An effect standing still at a place (a map spot, an explosion)."""
    kind = "static"

    def __init__(self, effect, transform=None, start=0.0, duration=3.0, events=None, target=None, name="static"):
        super().__init__(layers_of(effect), start, duration, events, name)
        self.M = np.eye(4) if transform is None else np.asarray(transform, float)
        self._target = None if target is None else np.asarray(target, float)

    def transform_at(self, t):
        return self.M

    def velocity_at(self, t):
        return np.zeros(3)

    def target_at(self, t):
        return self._target


# -- poses of a unit

class StaticPoses:
    """The pose a unit with no clip stands in: the bind pose, `inverse(InverseWorldTransform)` of each bone, which is where the
    mesh is drawn (checked on mephisto: its extent equals the mesh's). A real skeleton's `rest_locals()` are NOT the bind pose
    (`Skeleton.bind_error()` 5.95 there: its world frame is rotated against the mesh), so they are used only for a skeleton
    without `iwt`."""

    def __init__(self, skeleton):
        self.sk = skeleton
        iwt = getattr(skeleton, "iwt", None)
        self._w = np.linalg.inv(iwt) if iwt is not None else skeleton.worlds(skeleton.rest_locals())
        self.duration = 0.0

    def worlds(self, t: float) -> np.ndarray:
        return self._w

    def index(self, bone: str) -> int:
        return _bone_index(self.sk, bone)


class PlayerPoses:
    """Bone world matrices over a timeline of `view.player.Player`: `steps` = [(mode, seconds)] (as Player.timeline)."""

    def __init__(self, player, skeleton, steps, fps: float = FPS):
        self.sk, self.player, self.fps = skeleton, player, fps
        self.frames = player.timeline(steps, fps)
        self.duration = len(self.frames) / fps
        self._cache: dict = {}

    def worlds(self, t: float) -> np.ndarray:
        i = min(max(int(round(t * self.fps)), 0), len(self.frames) - 1)
        if i not in self._cache:
            self._cache[i] = self.player.pose(self.sk, frame=self.frames[i]).worlds
        return self._cache[i]

    def index(self, bone: str) -> int:
        return _bone_index(self.sk, bone)


def _bone_index(sk, bone: str) -> int:
    if bone in sk.idx:
        return sk.idx[bone]
    low = {n.lower(): i for n, i in sk.idx.items()}
    if bone.lower() in low:
        return low[bone.lower()]
    near = [n for n in sk.idx if bone.lower()[:4] in n.lower()][:6]
    raise KeyError("bone %r not in the skeleton (similar: %s)" % (bone, ", ".join(near) or "none"))


class AttachDriver(Driver):
    """The effect follows a bone of a posed unit: world = place x yaw/root scale x bone world x entity transform (xf)."""
    kind = "attach"

    def __init__(self, layers, poses, unit_scale: float = 1.0, place=None, yaw: float = 0.0, start: float = 0.0,
                 duration: float | None = None, events=None, name: str = "attach", pose_t0: float | None = None):
        super().__init__(layers, start, poses.duration if duration is None else duration, events, name)
        self.poses = poses
        self.pose_t0 = self.start if pose_t0 is None else float(pose_t0)      # driver time at which the poses are at 0
        self.place = np.eye(4) if place is None else np.asarray(place, float)
        self.R = _rot_y(yaw)
        self.R[:3, :3] *= unit_scale

    def _unit_frame(self, t):
        return self.place @ self.R

    def bone_world(self, t: float, bone: str | None) -> np.ndarray:
        """Bone world matrix in world space at driver time t (the unit's own frame when `bone` is empty)."""
        W = self._unit_frame(t)
        if not bone:
            return W
        return W @ self.poses.worlds(t - self.pose_t0)[self.poses.index(bone)]

    def layer_transform(self, t, layer):
        B = self.bone_world(t, layer.bone)
        if layer.inherit_only_position:
            B = _translate(B[:3, 3])
        return B @ layer.xf

    def bones_at(self, t):
        return lambda name=None: self.bone_world(t, name)

    def velocity_at(self, t):
        h = 0.02
        a, b = max(t - h, self.start), min(t + h, self.start + self.duration)
        if b - a < 1e-9:
            return np.zeros(3)
        la = self.layers[0]
        return (self.layer_transform(b, la)[:3, 3] - self.layer_transform(a, la)[:3, 3]) / (b - a)

    def context(self, t, i=0):
        c = super().context(t, i)
        c.velocity = self.velocity_at(t) if i == 0 else (
            (self.layer_transform(t + .02, self.layers[i])[:3, 3] - self.layer_transform(t - .02, self.layers[i])[:3, 3]) / .04)
        return c


def attach(effect, unit, bone: str | None = None, player=None, steps=None, poses=None, place=None, yaw: float = 0.0,
           start: float = 0.0, duration: float | None = None, skill: str | None = None, events=None) -> AttachDriver:
    """The effect on `bone` of `unit` (a scene.Unit: parts[0].skeleton, root_scale). `bone` None keeps each Binding's own
    bone (UnitEffects / Binding arguments), else the bone overrides it. Poses: `poses` (an object with worlds(t), index(bone),
    duration), or `player` + `steps` ([(mode, seconds)]), or the rest pose. `place` 4x4 and `yaw` (degrees) put the unit in
    the world; `skill` picks the entities whose UnitSourceSkillFilter matches."""
    lay = layers_of(effect, skill)
    if bone is not None:
        lay = [Layer(l.effect, l.xf, bone, l.inherit_only_position, l.label) for l in lay]
    sk = next((p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None), None) if poses is None else None
    if poses is None:
        if sk is None:
            raise ValueError("the unit has no skeleton: pass poses=")
        poses = PlayerPoses(player, sk, steps) if player is not None and steps else StaticPoses(sk)
    return AttachDriver(lay, poses, float(getattr(unit, "root_scale", 1.0) or 1.0), place, yaw, start, duration, events,
                        "attach:%s" % (bone or "binding"))


def attach_unit(fx: UnitEffects, unit, player=None, steps=None, poses=None, place=None, yaw=0.0, skill=None,
                start=0.0) -> list:
    """One AttachDriver per effect entity of `effects_of_unit` (each on its own bone), sharing the poses."""
    out = []
    for b in fx.vfx(skill):
        out.append(attach(b, unit, None, player, steps, poses, place, yaw, start, skill=skill))
        poses = out[-1].poses
    return out


# -- missiles

class MissileDriver(Driver):
    """Flight along a polyline with the row's velocity, acceleration and range. Faces the direction of flight (+z at yaw 0)."""
    kind = "missile"

    def __init__(self, layers, path, vel_units_s: float, accel: float = 0.0, max_vel: float | None = None,
                 range_s: float | None = None, arc: float = 0.0, tail: float = 1.0, events=None, start: float = 0.0,
                 name: str = "missile", stepped: bool = False):
        self.stepped = bool(stepped) and bool(accel)   # the game's per-frame path updates (ACCEL_EVERY); else smooth
        self._steps = None
        self.index = 0                                  # MissileIndex: the missile's number in its skill's volley
        self.path = np.asarray(path, float)
        seg = np.linalg.norm(np.diff(self.path, axis=0), axis=1)
        self.seg_len = seg
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.cum[-1])
        self.v0, self.acc, self.vmax, self.arc = float(vel_units_s), float(accel), max_vel, float(arc)
        self.range_s = range_s
        t_reach = self.time_of_distance(self.length)
        self.t_end = t_reach if range_s is None else min(t_reach, range_s)
        self.reached = range_s is None or t_reach <= range_s + 1e-9
        self.end_pos = self.position_at(self.t_end)
        d = self.direction_at(self.t_end)
        te = start + self.t_end
        ev = [(start, "launch", {"pos": self.path[0].tolist()}),
              (te, "hit" if self.reached else "expire", {"pos": self.end_pos.tolist(), "dir": d.tolist()}),
              (te, "explode", {"pos": self.end_pos.tolist(), "dir": d.tolist()})]
        super().__init__(layers, start, self.t_end + tail, (events or []) + ev, name)
        # The game stops a missile's effect when the missile dies (its binding's hardKillOnDestroy false: a stop, not a
        # kill; lane r3-calibfix). Before, nothing stopped it: Frost Nova's ring and smoke, which end on
        # effect.isRunning, lived on ~0.25 s past the game's.
        self.stop_t = te

    def attrs_at(self, t: float, index: int = 0) -> dict:
        """The effect attributes the game sets on a missile's effect (lane calib3; Frost Nova's ring grows by
        MissileCurrentFrame / MissileFrames, the file default 0 / 0 gives NaN): MissileFrames = the row's Range (frames),
        MissileCurrentFrame = frames flown (25 per second, held at the range), MissileSlowFactor 1 (not slowed; GUESS),
        MissileIndex = `index`. Pass them as Sim attrs before each step."""
        frames = (self.range_s if self.range_s is not None else self.t_end) * FPS
        cur = min(max(t - self.start, 0.0) * FPS, frames)
        if MISSILE_FRAME_COUNTS_DOWN:
            cur = frames - cur
        return {"MissileFrames": [float(frames), 0.0, 0.0, 0.0], "MissileCurrentFrame": [float(cur), 0.0, 0.0, 0.0],
                "MissileSlowFactor": [1.0, 0.0, 0.0, 0.0], "MissileIndex": [int(index), 0.0, 0.0, 0.0]}

    def _step_table(self, frames: int):
        """Cumulative distance after each game frame (0..frames) of the stepped model (module constants)."""
        if self._steps is not None and len(self._steps) > frames:
            return self._steps
        n = max(int(frames), 64)
        cum, v, acc = [0.0], self.v0, self.acc
        dv = acc * ACCEL_EVERY / FPS                    # units/s added at every ACCEL_EVERY-th frame
        for k in range(1, n + 1):
            if acc and k % ACCEL_EVERY == 0:
                v += dv
                if self.vmax is not None and v > self.vmax:
                    v, acc = self.vmax, 0.0
                elif v < 0.0:
                    v = 0.0
            cum.append(cum[-1] + v / FPS)
        self._steps = np.asarray(cum)
        self._v_end = v
        return self._steps

    def distance_at(self, t: float) -> float:
        t = max(t, 0.0)
        if self.stepped:                               # frame k moves at that frame's velocity; linear inside a frame
            f = t * FPS
            k = int(f)
            tab = self._step_table(min(k + 1, 100000))
            if k + 1 < len(tab):
                return float(tab[k] + (f - k) * (tab[k + 1] - tab[k]))
            return float(tab[-1] + (f - (len(tab) - 1)) * self._v_end / FPS)
        if self.acc < 0:                               # decelerating: stops, never flies back
            ts = self.v0 / -self.acc if self.v0 > 0 else 0.0
            t = min(t, ts)
            return self.v0 * t + 0.5 * self.acc * t * t
        if self.vmax is None or self.acc <= 0 or self.v0 >= self.vmax:
            return self.v0 * t + 0.5 * self.acc * t * t if self.vmax is None or self.acc <= 0 else self.vmax * t
        t_cap = (self.vmax - self.v0) / self.acc
        if t <= t_cap:
            return self.v0 * t + 0.5 * self.acc * t * t
        return self.v0 * t_cap + 0.5 * self.acc * t_cap ** 2 + self.vmax * (t - t_cap)

    def time_of_distance(self, d: float) -> float:
        if d <= 0:
            return 0.0
        lo, hi = 0.0, 1.0
        while self.distance_at(hi) < d and hi < 1e5:
            if (self.v0 <= 0 and self.acc <= 0) or (self.acc < 0 and self.distance_at(2.0 * hi) <= self.distance_at(hi)):
                return math.inf                        # never gets there (stopped)
            hi *= 2.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if self.distance_at(mid) < d else (lo, mid)
        return hi

    def position_at(self, t: float) -> np.ndarray:           # t: seconds since launch
        s = min(self.distance_at(t), self.length)
        i = int(min(np.searchsorted(self.cum, s, side="right") - 1, len(self.seg_len) - 1))
        f = (s - self.cum[i]) / self.seg_len[i] if self.seg_len[i] > 0 else 0.0
        p = self.path[i] + f * (self.path[i + 1] - self.path[i])
        if self.arc:
            u = s / self.length if self.length > 0 else 1.0
            p = p + np.array([0.0, 4.0 * self.arc * u * (1.0 - u), 0.0])
        return p

    def direction_at(self, t: float) -> np.ndarray:
        s = min(self.distance_at(t), self.length)
        i = int(min(np.searchsorted(self.cum, s, side="right") - 1, len(self.seg_len) - 1))
        d = self.path[i + 1] - self.path[i]
        if self.arc and self.length > 0:
            u = s / self.length
            d = d / max(np.linalg.norm(d), 1e-9) * self.length + np.array([0.0, 4.0 * self.arc * (1.0 - 2.0 * u), 0.0]) * 1.0
        n = np.linalg.norm(d)
        return d / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])

    def transform_at(self, t):
        lt = min(max(t - self.start, 0.0), self.t_end)
        d = self.direction_at(lt)
        M = _translate(self.position_at(lt)) @ _rot_y(_yaw_of(d))
        return M

    def velocity_at(self, t):
        lt = t - self.start
        if lt < 0 or lt >= self.t_end:
            return np.zeros(3)
        h = 1e-3
        return (self.position_at(lt + h) - self.position_at(max(lt - h, 0.0))) / (h + min(h, lt))

    def target_at(self, t):
        return self.path[-1]

    def position(self, t):                      # driver time -> world
        return self.position_at(min(max(t - self.start, 0.0), self.t_end))


def missile(effect, start=None, end=None, path=None, speed: float | None = None, row: dict | None = None,
            accel: float | None = None, max_speed: float | None = None, range_frames: float | None = None,
            events: dict | None = None, arc: float = 0.0, start_time: float = 0.0, tail: float = 1.0,
            skill: str | None = None) -> MissileDriver:
    """A flying missile. `path` (>= 2 points) or `start` + `end` (world xyz). Speed in world units per second, or `row`:
    a MissileEffects (or its `row` facts / a missiles.txt row) giving Vel, Accel, MaxVel, Range. `events` = {"hit": x,
    "explode": x}: an effect (path, Binding, MissileEffects ...) that spawns at the event, as a child driver at the impact
    point facing the flight direction (`driver.children`: [(t, StaticDriver)]); `explode` is also used when the missile
    expires at its range."""
    facts = None
    if row is not None:
        facts = row.row if hasattr(row, "row") else row
        if facts and "speed_units_s" not in facts:
            facts = missile_facts(facts)
    if path is None:
        if start is None or end is None:
            raise ValueError("missile needs path= or start= and end=")
        path = [start, end]
    v = speed if speed is not None else (facts or {}).get("speed_units_s")
    if v is None:
        raise ValueError("missile needs speed= or a missiles.txt row (Vel)")
    ac = accel if accel is not None else (facts or {}).get("accel_units_s2", 0.0)
    mx = max_speed if max_speed is not None else (facts or {}).get("max_speed_units_s")
    rf = range_frames if range_frames is not None else ((facts or {}).get("range") or None)
    drv = MissileDriver(layers_of(effect, skill), path, v, ac or 0.0, mx, (rf / FPS) if rf else None, arc, tail, None, start_time,
                        "missile", stepped=accel is None and bool(facts))
    for evname in ("hit", "explode"):
        x = (events or {}).get(evname)
        if x is None:
            continue
        te = start_time + drv.t_end
        # the effect starts at the event; `hit` and `explode` on the same instant are two separate spawns
        d = drv.direction_at(drv.t_end)
        M = _translate(drv.end_pos) @ _rot_y(_yaw_of(d))
        child = StaticDriver(x, M, start=te, duration=3.0, events=[(te, evname, {"pos": drv.end_pos.tolist()})],
                             name="%s:%s" % (evname, "missile"))
        drv.children.append((te, child))
        drv.events.append((te, "spawn", {"effect": child.layers[0].effect if child.layers else None, "event": evname}))
    drv.events.sort(key=lambda e: e[0])
    return drv


# -- skills

class SkillDriver(Driver):
    """A cast: the unit's mode timeline, a `cast` event at the trigger frame, and child drivers (overlays on the unit,
    missiles from a bone) that start at it."""
    kind = "skill"

    def __init__(self, poses, trigger_t: float, mode: str, duration: float, children, events):
        super().__init__([], 0.0, duration, events, "skill")
        self.poses, self.trigger_t, self.mode = poses, trigger_t, mode
        self.children = children
        self.skipped: list = []           # [(entity, why)] timeline effects left out (bone not in the skeleton)


def cast_time(player, mode, frame: float, fps: float = FPS) -> float:
    """Seconds after the mode starts at which classic frame `frame` of the mode plays: the player's mode length x
    frame / the mode's classic frame count (`player.classic[token]['frames']`), else frame / 25 (the classic rate)."""
    from hdkit.view import player as P
    tok = P.MODE_TOKENS.get(mode, str(mode)) if isinstance(mode, int) else str(mode).lower()
    meta = (player.classic or {}).get(tok) if player is not None else None
    if player is not None and meta and meta.get("frames"):
        return player.length(tok) * float(frame) / float(meta["frames"])
    return float(frame) / fps


def skill(effects, unit, cast_frame: float, mode: str = "sc", player=None, poses=None, place=None, yaw: float = 0.0,
          target=None, bone: str | None = None, hold: float = 0.5, skill_name: str | None = None, unit_fx=None) -> SkillDriver:
    """A skill cast. `effects`: a SkillEffects (from effects_of_skill) or a list of (kind, effect[, options]) with kind
    `overlay` (attached to the unit's root, following it) or `missile` (leaves `bone` toward `target`, a world xyz or
    skill-row driven when a MissileEffects is given). The unit plays `mode` once (Player timeline) then holds `hold`
    seconds; the effects start at `cast_frame`'s time. `unit_fx` (an `effects_of_unit` result) adds the unit's animation timeline
    effects of `mode` as children at their event times (`timeline_children`)."""
    steps = [(mode, 0.0)]
    if player is not None:
        L = max(player.length(mode), 0.04)
        steps = [(mode, L), ("nu", hold)] if hold else [(mode, L)]
        tt = cast_time(player, mode, cast_frame)
        if poses is None:
            sk = next(p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None)
            poses = PlayerPoses(player, sk, steps)
        dur = poses.duration
    else:
        tt = float(cast_frame) / FPS
        dur = tt + hold
        if poses is None:
            sk = next(p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None)
            poses = StaticPoses(sk)
    items = []
    if isinstance(effects, SkillEffects):
        skill_name = skill_name or effects.name
        items = [(r.kind, r.effects) for r in effects.refs if r.effects is not None]
    else:
        items = [(it[0], it[1]) for it in effects]
    children, events = [], [(tt, "cast", {"mode": mode, "frame": cast_frame})]
    sc = float(getattr(unit, "root_scale", 1.0) or 1.0)
    for kind, fx in items:
        if kind == "overlay":
            d = AttachDriver(layers_of(fx, skill_name), poses, sc, place, yaw, tt, 3.0, None, "overlay")
            d.layers = [Layer(l.effect, l.xf, None, l.inherit_only_position, l.label) for l in d.layers]
            children.append((tt, d))
        elif kind == "missile":
            if target is None:
                continue
            tmp = AttachDriver([Layer(None)], poses, sc, place, yaw)
            org = tmp.bone_world(tt, bone)[:3, 3]
            facts = fx.row if hasattr(fx, "row") else None
            try:
                m = missile(fx, start=org, end=target, row=facts or None, speed=None if facts else 20.0,
                            start_time=tt, skill=skill_name)
            except ValueError:
                continue
            children.append((tt, m))
    skipped: list = []
    if unit_fx is not None:
        tc, tev, skipped = timeline_children(unit_fx, mode, poses, sc, place, yaw, player, skill_name)
        children += tc
        events += tev
    d = SkillDriver(poses, tt, mode, dur, sorted(children, key=lambda c: c[0]), sorted(events, key=lambda e: e[0]))
    d.skipped = skipped
    return d


def event_time(player, mode, t: float) -> float:
    """A timeline event's time in the animation clip (seconds) -> seconds into the player's run of `mode` (the player stretches
    the clip to the mode's length; no player or no clip = unchanged)."""
    try:
        clip = player.clips.get(player.sm.clip_name(mode, player.wclass))
        L = player.length(mode)
        return t * L / clip.duration if clip is not None and clip.duration > 0 and L > 0 else t
    except Exception:    # noqa: BLE001 - a stub or partial player: the clip time stands
        return t


def timeline_children(unit_fx, mode: str, poses, unit_scale: float = 1.0, place=None, yaw: float = 0.0, player=None,
                      skill: str | None = None, life: float = 3.0) -> tuple[list, list, list]:
    """The animation-timeline effects of unit mode `mode` as drivers: ([(t, driver)], [(t, "timeline", payload)], skipped).
    At the event's time (`Binding.time` scaled to the mode's length) an attached effect (`attach`, or SpawnObjectEffect, or
    attachToDeadBody) follows its bone through the clip; otherwise it is left standing where the bone was at that moment.
    `life` seconds is how long it is simulated (the event's own `duration` when set). `root` and a unitPart the skeleton lacks
    mean the unit's frame; any other missing bone skips the event (named in `skipped`)."""
    children, events, skipped = [], [], []
    tmp = AttachDriver([Layer(None)], poses, unit_scale, place, yaw)
    for b in unit_fx.timeline(mode):
        if not b.file or not b.applies(skill, mode):
            continue
        te = event_time(player, mode, b.time or 0.0)
        dur = float(b.extra.get("duration") or 0.0) or life
        bone = b.bone
        if bone:
            try:
                poses.index(bone)
            except KeyError:             # `root` is the unit's frame, a SpawnObjectEffect `unitPart` (special1) a model part: no bone
                if bone.lower() == "root" or b.extra.get("unit_part"):
                    bone = None
        lay = Layer(b.file, b.xf, bone, b.inherit_only_position, b.entity)
        follow = b.attach or b.extra.get("attach_to_dead_body") or b.attach is None
        try:
            if follow:
                d = AttachDriver([lay], poses, unit_scale, place, yaw, te, dur, None, "timeline:" + b.entity, pose_t0=0.0)
                d.bones_at(te)(bone)
            else:
                M = tmp.bone_world(te, bone)
                if b.inherit_only_position:
                    M = _translate(M[:3, 3])
                d = StaticDriver(Layer(b.file, b.xf, None, False, b.entity), M, te, dur, None, name="timeline:" + b.entity)
        except KeyError as e:
            skipped.append((b.entity, str(e)))
            continue
        children.append((te, d))
        events.append((te, "timeline", {"file": b.file, "bone": bone, "mode": b.mode, "attach": bool(follow)}))
    return children, events, skipped


def timeline_driver(unit_fx, unit, mode: str, player=None, poses=None, place=None, yaw: float = 0.0, hold: float = 0.5,
                    skill: str | None = None, life: float = 3.0) -> SkillDriver:
    """The unit plays `mode` once (then holds `hold` seconds) and the effects of its animation timeline for that mode start at
    their event times: a driver whose children `drive()` plays. Without a player the mode lasts the latest event + `hold`."""
    sk = next((p.skeleton for p in unit.parts if getattr(p, "skeleton", None) is not None), None) if poses is None else None
    sc = float(getattr(unit, "root_scale", 1.0) or 1.0)
    if poses is None:
        if player is not None:
            L = max(player.length(mode), 0.04)
            poses = PlayerPoses(player, sk, [(mode, L), ("nu", hold)] if hold else [(mode, L)])
        else:
            poses = StaticPoses(sk)
    children, events, skipped = timeline_children(unit_fx, mode, poses, sc, place, yaw, player, skill, life)
    dur = poses.duration or (max([t for t, _c in children] or [0.0]) + hold)
    d = SkillDriver(poses, min([t for t, _c in children] or [0.0]), mode, dur, children, events)
    d.skipped = skipped
    return d


# -- a whole cast: the caster's overlays, the skill's missiles (count, launch point, path) as the game makes them
#
# Sources: the D2 game code as decompiled by D2MOO (github.com/ThePhrozenKeep/D2MOO, MIT, credited in THIRD_PARTY.md):
# SKILLS_SrvDo017_ChargedBolt_BoltSentry (D2Game SkillSor.cpp) makes `calc1` missiles at the caster's own sub-tile toward the
# target; SKILLS_MissileInit_ChargedBolt caps the life at 77 frames, seeds each bolt with (its index + the first path point's x)
# and gives it PATHTYPE_CHARGEDBOLT; PATH_ComputePathChargedBolt (D2Common PathMisc.cpp) lays (life >> 1) points 2 sub-tiles
# apart, each step in the main 8-way direction to the target turned by -1, 0 or +1 (a 32-entry table, indexed by the seed's
# roll & 31, one 0 missing). SEED_RollRandomNumber: seed = lo * 0x6AC690C5 + hi (64-bit), returns the new low half.

D2_DIR_OFFSETS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))   # gatDirectionToOffset
_CB_TURNS = tuple([-1, 0, 1] * 10 + [-1, 1])                                              # getChargedBoltDirOffset


class D2Seed:
    """The game's seed (D2SeedStrc): `roll()` = SEED_RollRandomNumber; `D2Seed.low(n)` = SEED_InitLowSeed (high half 666)."""

    def __init__(self, lo: int, hi: int = 666):
        self.lo, self.hi = lo & 0xFFFFFFFF, hi & 0xFFFFFFFF

    @classmethod
    def low(cls, n: int) -> "D2Seed":
        return cls(n, 666)

    def roll(self) -> int:
        v = self.lo * 0x6AC690C5 + self.hi
        self.lo, self.hi = v & 0xFFFFFFFF, (v >> 32) & 0xFFFFFFFF
        return self.lo


def direction8(dx: float, dy: float) -> int:
    """The nearest of the game's 8 path directions (D2_DIR_OFFSETS index, 0 = +x, 2 = +y) to a sub-tile vector."""
    return int(round(math.atan2(dy, dx) / (math.pi / 4.0))) % 8


def chargedbolt_path(start, target, frames: int, seed: D2Seed) -> list:
    """PATH_ComputePathChargedBolt: (frames >> 1) + 1 points in sub-tiles from `start` (the caster's sub-tile), 2 sub-tiles
    a step, each step the main direction to `target` turned by -1/0/+1 (`seed.roll() & 31` into the game's table)."""
    main = direction8(target[0] - start[0], target[1] - start[1])
    x, y = int(round(start[0])), int(round(start[1]))
    pts = []
    for _ in range(int(frames) >> 1):
        pts.append((x, y))
        d = (main + _CB_TURNS[seed.roll() & 31]) % 8
        x += 2 * D2_DIR_OFFSETS[d][0]
        y += 2 * D2_DIR_OFFSETS[d][1]
    pts.append((x, y))
    return pts


def skill_calc(expr: str, row: dict | None, level: int = 1) -> float:
    """A skills.txt calc column (`min(24,ln12)`, `par1+lvl`, ...) at skill level `level`: numbers, + - * / ( ), min, max, lvl /
    blvl, par1..par8, linear `lnXY` and diminishing `dmXY` pairs. Other functions (skill(), stat refs) raise ValueError."""
    import ast
    row = row or {}
    par = {i: _num(row.get("param%d" % i)) for i in range(1, 9)}
    names = {"lvl": float(level), "blvl": float(level), **{"par%d" % i: v for i, v in par.items()}}
    pct = 110 * level // (level + 6) if level > 0 else 0
    for a, b in ((1, 2), (3, 4), (5, 6), (7, 8)):
        names["ln%d%d" % (a, b)] = par[a] + (level - 1) * par[b]
        names["dm%d%d" % (a, b)] = min(par[b], par[a] + int((par[b] - par[a]) * pct / 100)) if level > 0 else 0
    ops = {ast.Add: lambda x, y: x + y, ast.Sub: lambda x, y: x - y, ast.Mult: lambda x, y: x * y,
           ast.Div: lambda x, y: x / y if y else 0.0}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id.lower() in names:
            return names[n.id.lower()]
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id.lower() in ("min", "max") and n.args:
            return (min if n.func.id.lower() == "min" else max)(ev(x) for x in n.args)
        raise ValueError("skill calc %r: %s not supported" % (expr, ast.dump(n)[:60]))
    return ev(ast.parse(str(expr).strip(), mode="eval"))


# srvdofunc -> how the skill makes its missiles (only what is ported; any other func: one missile straight to the target)
SKILL_MISSILE_FUNCS = {
    17: {"name": "chargedbolt", "count": "calc1", "path": "chargedbolt", "max_frames": 77},   # Charged Bolt, Bolt Sentry
    22: {"name": "nova", "path": "ring", "vel_calc": "calc1"},     # SKILLS_SrvDo022_NovaAttack: Frost Nova, Nova ...
    24: {"name": "firewall", "path": "firewall"},   # SKILLS_SrvDo024_FireWall: a line of fires (fx/wallcast.py)
}

# SKILLS_CreateMissileRing (D2MOO, MIT; D2Game 0x6FD14140): a nova is always 64 missiles aimed at these sub-tile offsets
# (radius 30, a circle starting at +x and turning towards +y), velocity = the skill's calc1 + the row's Vel (+VelLev).
RING_DX = (30, 29, 29, 28, 27, 26, 24, 23, 21, 19, 16, 14, 11, 8, 5, 2, 0, -2, -5, -8, -11, -14, -16, -19, -21, -23, -24,
           -26, -27, -28, -29, -29, -30, -29, -29, -28, -27, -26, -24, -23, -21, -19, -16, -14, -11, -8, -5, -2, 0, 2, 5,
           8, 11, 14, 16, 19, 21, 23, 24, 26, 27, 28, 29, 29)
RING_DY = tuple(RING_DX[(i - 16) % 64] for i in range(64))


def animdata_record(data: bytes, cof: str) -> dict | None:
    """One record of an animdata.d2 (256 buckets: uint32 count, then 160-byte records: name[8], frames, speed, events[144]):
    {"frames", "speed", "action": first frame with an event (the attack / missile frame) or None}. `cof` like `SOSCBOW`."""
    import struct
    want, o = cof.upper().encode("ascii"), 0
    for _ in range(256):
        if o + 4 > len(data):
            break
        n = struct.unpack_from("<I", data, o)[0]
        o += 4
        for _k in range(n):
            rec = data[o:o + 160]
            o += 160
            if rec[:8].split(b"\0")[0].upper() == want:
                frames, speed = struct.unpack_from("<II", rec, 8)
                ev = rec[16:16 + min(frames, 144)]
                act = next((i for i, e in enumerate(ev) if e), None)
                return {"frames": int(frames), "speed": int(speed), "action": act}
    return None


class _RootPoses:
    """Poses of a caster drawn as its root only (no skeleton loaded): every bone is the unit frame."""

    def __init__(self, duration: float):
        self.duration = duration

    def worlds(self, t):
        return np.eye(4)[None]

    def index(self, bone):
        return 0


CAST_OVERLAY_COLS = ("castoverlay", "cltoverlaya", "cltoverlayb")    # drawn on the caster (tgt/sum/prg/srv: not the cast)


def overlay_seconds(row: dict | None, fs=None) -> float | None:
    """One pass of an overlay.txt row in seconds: Frames x 16 / AnimRate client frames at 25 fps (lane r3-overlays, 1.14d
    GFX_AddOverlayToUnit 0x470390: frame delay = frames << 8, rate = AnimRate << 4 per frame; GFX_UpdateOverlayAnimations
    0x470c30 drops a play-once overlay, a skill's cast overlay, at the end of the pass and the HD effect is stopped).
    The classic sprite supplies Frames when the table leaves it blank. None without a frame count or AnimRate."""
    if not row:
        return None
    frames, rate = _num(row.get("frames")), _num(row.get("animrate"))
    if frames <= 0 and rate > 0 and fs is not None and row.get("filename"):
        from hdkit.formats.dcc import frame_count

        try:
            frames = frame_count(fs.read(f"global/overlays/{row['filename']}.dcc"))
        except (OSError, KeyError, ValueError):
            return None
    if frames <= 0 or rate <= 0:
        return None
    return frames * 16.0 / rate / FPS



def cast(skill_fx: "SkillEffects", unit=None, toward=(12.0, 0.0), level: int = 1, seed: int = 0, anim: dict | None = None,
         player=None, poses=None, place=None, yaw: float | None = None, start_subtile=(0, 0), hold: float = 0.5,
         overlay_at: str = "start", fs=None) -> SkillDriver:
    """A whole cast of `skill_fx` (effects_of_skill) by a caster standing at the origin (`place` moves it), aimed `toward`
    (dx, dy harness sub-tiles: world x = 2 dx, z = 2 dy). Driver time 0 = the cast mode starts. Children:
      - the caster's overlays (`CAST_OVERLAY_COLS`) on the unit root from the cast mode's start (`overlay_at="start"`,
        default: MEASURED by lane r5-casttiming on 27 ms shots of Fire Bolt, Charged Bolt and Frost Nova, r5-casttiming/t1:
        the cast light rises with the caster's first cast pose, the missiles leave at the mode's action frame 0.123 s
        later with the bench character's staff, SOSCSTF, and the light then follows its own curves at 1x; the earlier
        `lead` / `cast` readings used a bow's action frame, 0.28 s), CAST_OVERLAY_LEAD_S before the action frame
        (`overlay_at="lead"`) or at it (`overlay_at="cast"`),
      - the missiles at the mode's action frame (`anim` = animdata_record of the caster's COF: action x 256 / speed / 25 s),
        from the caster's sub-tile (D2: missile x, y = the unit's; the height is the HD missile's own transform), with the
        skill's count and path (`SKILL_MISSILE_FUNCS` by srvdofunc: Charged Bolt's calc1 bolts on their seeded random walks;
        else one straight missile), the row's Vel and Range (capped for Charged Bolt at 77 frames).
    `unit` (scene.Unit) + `player` pose the caster (PlayerPoses over [(mode, length), ("nu", hold)]); without them every bone
    is the unit root. `driver.cast_info` lists what was made (counts, paths, times, assumptions)."""
    row = skill_fx.row or {}
    mode = skill_fx.mode
    anim = anim or {}
    spd = float(anim.get("speed") or 256) or 256.0
    tick = 256.0 / spd / FPS                                   # seconds per animation frame
    mode_len = float(anim.get("frames") or 0) * tick or (player.length(mode) if player is not None else 0.56)
    act = anim.get("action")
    tt = act * tick if act is not None else 0.0
    d = np.array([2.0 * toward[0], 0.0, 2.0 * toward[1]], float)
    if yaw is None:
        yaw = _yaw_of(d)
    if poses is None:
        sk = next((p.skeleton for p in getattr(unit, "parts", []) if getattr(p, "skeleton", None) is not None), None)
        if sk is not None and player is not None:
            poses = PlayerPoses(player, sk, [(mode, mode_len), ("nu", hold)])
        elif sk is not None:
            poses = StaticPoses(sk)
        else:
            poses = _RootPoses(mode_len + hold)
    sc = float(getattr(unit, "root_scale", 1.0) or 1.0) if unit is not None else 1.0
    P = np.eye(4) if place is None else np.asarray(place, float)
    children, events = [], [(0.0, "mode", {"mode": mode}), (tt, "cast", {"mode": mode, "frame": act})]
    info = {"skill": skill_fx.name, "mode": mode, "mode_seconds": round(mode_len, 4), "action_frame": act,
            "launch_s": round(tt, 4), "yaw": round(yaw, 2), "overlays": [], "missiles": []}
    for r in skill_fx.refs:
        if r.kind != "overlay" or r.effects is None:
            continue
        cols = r.column.split(",")
        if not any(c in CAST_OVERLAY_COLS for c in cols):
            info["overlays"].append({"name": r.name, "column": r.column, "drawn": False})
            continue
        lay = [Layer(l.effect, l.xf, None, l.inherit_only_position, l.label) for l in layers_of(r.effects, skill_fx.name)]
        if not lay:
            continue
        t_ov = {"cast": tt, "start": 0.0}.get(overlay_at, max(tt - CAST_OVERLAY_LEAD_S, 0.0))
        dr = AttachDriver(lay, poses, sc, P, yaw, t_ov, 3.0, None, "overlay:" + r.name, pose_t0=0.0)
        dr.stop_s = overlay_seconds(r.effects.row, fs)  # effect age at which the game stops the overlay's effect
        children.append((t_ov, dr))
        info["overlays"].append({"name": r.name, "column": r.column, "drawn": True, "start": round(t_ov, 4),
                                 "stop_s": None if dr.stop_s is None else round(dr.stop_s, 4)})
    func = SKILL_MISSILE_FUNCS.get(int(_num(row.get("srvdofunc"))))
    if func and func.get("path") == "firewall":
        from . import wallcast
        fire_cast = wallcast.children(skill_fx, toward, level, tt, P, start_subtile)
    else:
        from . import firemakers
        fire_cast = firemakers.children(skill_fx, toward, level, tt, P, start_subtile, mode_len)
    if fire_cast is not None:
        ch, mi = fire_cast
        children += ch
        info.update(mi)
    for r in ([] if fire_cast is not None else skill_fx.refs):
        if r.kind != "missile" or r.effects is None:
            continue
        facts = dict(r.effects.row or {})
        if not facts:
            continue
        n = 64 if func and func.get("path") == "ring" else 1
        if func and func.get("count"):
            try:
                n = max(1, int(skill_calc(row.get(func["count"], "1") or "1", row, level)))
            except ValueError:
                n = 1
        rng = facts.get("range") or 0.0
        if func and func.get("max_frames") and rng > func["max_frames"]:
            rng = func["max_frames"]                    # SKILLS_MissileInit_ChargedBolt: >= 78 frames -> 77
        sx, sy = start_subtile
        tgt = (sx + toward[0], sy + toward[1])
        speed = None
        if func and func.get("vel_calc"):              # NovaAttack: velocity = calc1 + Vel (+ level x VelLev / 8)
            try:
                c1 = skill_calc(row.get(func["vel_calc"], "") or "0", row, level)
            except ValueError:
                c1 = 0.0
            vel = facts.get("vel", 0.0) + (level * _num((r.effects.row or {}).get("vellev"))) // 8
            speed = (float(c1) + vel) * VEL_UNITS_PER_S
        for i in range(n):
            if func and func.get("path") == "chargedbolt":
                pts = chargedbolt_path((sx, sy), tgt, int(rng), D2Seed.low(seed + i + int(sx)))
            elif func and func.get("path") == "ring":
                far = max(1.0, (speed or facts.get("speed_units_s", 20.0)) * (rng or 50) / FPS / UNITS_PER_SUBTILE * 1.5)
                u = np.array([RING_DX[i], RING_DY[i]], float) / 30.0
                pts = [(sx, sy), (sx + u[0] * far, sy + u[1] * far)]
            else:
                far = max(1.0, facts.get("speed_units_s", 20.0) * (rng or 50) / FPS / UNITS_PER_SUBTILE * 1.5)
                u = np.asarray(toward, float) / max(np.linalg.norm(toward), 1e-9)
                pts = [(sx, sy), (sx + u[0] * far, sy + u[1] * far)]
            world = [(P @ np.array([UNITS_PER_SUBTILE * (x - sx), 0.0, UNITS_PER_SUBTILE * (y - sy), 1.0]))[:3] for x, y in pts]
            # drop repeated points (a path that turns back on itself keeps its corners)
            path = [world[0]] + [w for a_, w in zip(world, world[1:]) if np.linalg.norm(w - a_) > 1e-9]
            if len(path) < 2:
                continue
            m = missile(r.effects, path=path, row=facts, range_frames=rng or None, start_time=tt, skill=skill_fx.name)
            if speed is not None:
                m = missile(r.effects, path=path, row=dict(facts, speed_units_s=speed), range_frames=rng or None,
                            start_time=tt, skill=skill_fx.name)
            m.name = "missile:%s#%d" % (r.name, i)
            m.index = i
            children.append((tt, m))
            info["missiles"].append({"name": r.name, "index": i, "path_subtiles": [list(map(float, p)) for p in pts],
                                     "range_frames": rng, "t_end": round(m.t_end, 4)})
        info["missile_func"] = func["name"] if func else None
        info["count"] = n
    dur = max([mode_len + hold] + [c[0] + c[1].duration for c in children])
    drv = SkillDriver(poses, tt, mode, dur, children, sorted(events, key=lambda e: e[0]))
    drv.cast_info = info
    drv.yaw = yaw
    return drv


# -- maps

@dataclass
class PostPreset:
    """What the render lane needs of the game's post chain (docs/hdfx.md section 6): exposure multiplies the lit HDR
    colour, the tone chain is hdkit.view.tone ("game" grey curve, "lut" the full colour path), bloom as tone.bloom."""
    exposure: float = 1.0
    tone: str = "srgb"
    bloom: dict | None = None            # {cutoff, intensity, levels, gain}
    peak: float | None = None            # display peak (nits) of the "lut" chain
    curve: list | None = None            # tone.max_rgb_curve parameters (unknown: None = off)
    value: dict | None = None
    lut: str | None = None               # grading LUT path (not decoded, not applied)
    source: str = ""
    notes: list = field(default_factory=list)

    def as_kwargs(self) -> dict:
        """The `post=` argument of FxRenderer.render: bloom, tonemap, exposure."""
        return {"bloom": self.bloom, "tonemap": self.tone, "exposure": self.exposure}

    def as_dict(self) -> dict:
        return {"exposure": self.exposure, "tone": self.tone, "bloom": self.bloom, "peak": self.peak, "curve": self.curve,
                "value": self.value, "lut": self.lut, "source": self.source, "notes": self.notes}


@dataclass
class LightPreset:
    name: str
    light: object                        # gpu.Light of the scene (key, ambient, point lights)
    post: PostPreset
    vis: str | None = None
    unknown: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    rig: object = None


def light_preset(vis=None, light_mask: int | None = 1, tone: str = "lut", bloom_gain: float = 1.0, name: str = "") -> LightPreset:
    """The game's light, exposure, tone chain and bloom for an effect in a scene, from one `env/vis/*.json` (a path, bytes
    or the parsed dict), else the generic `game` rig. Everything comes from `view.rig` (exposure 0.18 / (2^EV x 10/65),
    key and fill lights by lightMask `light_mask`: 1 for the environment, 19 for monsters, 68 for the player), `view.tone`
    (tone "lut": the LUT colour path to the display peak 250 nits; "game": the grey curve) and the vis file's bloomCutoff /
    bloomIntensity. What the viewer does not know stays listed in `unknown` (grading LUT, max-RGB curve, bloom level
    weights: `bloom_gain` stands in for them)."""
    from hdkit.view import rig as rigmod
    path, tmp = None, None
    if vis is not None:
        if isinstance(vis, (str, Path)) and Path(vis).is_file():
            path = str(vis)
        else:
            data = vis if isinstance(vis, bytes) else json.dumps(vis).encode()
            tmp = tempfile.TemporaryDirectory()
            path = str(Path(tmp.name) / "vis.json")
            Path(path).write_bytes(data)
    try:
        r = rigmod.from_vis(path, light_mask=light_mask) if path else rigmod.game()
        vd = rigmod._visual_data(path) if path else {}
    finally:
        if tmp:
            tmp.cleanup()
    r.tone = tone
    post = PostPreset(r.exposure, tone, None, None, None, None, vd.get("gradingLutFile"), r.meta.get("source", "game rig"))
    if tone == "lut":
        post.peak = rigmod.tone.PEAK_NITS if hasattr(rigmod, "tone") else 250.0
        if vd:
            post.bloom = {"cutoff": float(vd.get("bloomCutoff", 1.0)), "intensity": float(vd.get("bloomIntensity", 1.0)),
                          "levels": 5, "gain": float(bloom_gain)}
            pts = vd.get("characterContrastPoints") or {}
            post.value = {"points": [float(pts.get(k, d)) for k, d in zip("xyzw", (0.25, 0.25, 0.75, 0.99))],
                          "brightness": 0.0, "strength": 0.0}
        post.notes.append("tone 'lut' = cs_lut_combine_c colour path; max-RGB curve off (values unknown); bloom level weights "
                          "equal x bloom_gain (assumed)")
        r.post = {"peak": post.peak, "bloom": post.bloom} if post.bloom else {"peak": post.peak}
    else:
        post.notes.append("tone 'game': hue-preserving grey curve (tone.apply)")
    post.notes += list(r.unknown)
    return LightPreset(name or (Path(path).stem if path else "game"), r.light, post, path and Path(path).name,
                       list(r.unknown), list(r.assumptions), r)


@dataclass
class SceneContext:
    """A place on an HD map as the effect renderer needs it."""
    map: str
    xy: tuple                            # the cell coordinates asked for
    world: tuple                         # (x, ground y, z) in world units
    preset: str | None
    biome: str | None
    vis: str | None
    light: LightPreset
    lights: list                         # preset point lights near the spot (gpu point-light dicts)
    frame: object = None                 # mapview Frame (cells around the spot), for the base scene
    camera: dict = field(default_factory=dict)
    roots: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    ground_y: float = 0.0

    def place(self, effect, offset=(0.0, 0.0, 0.0), yaw: float = 0.0, start: float = 0.0, duration: float = 3.0,
              skill: str | None = None) -> StaticDriver:
        """The effect standing on the spot (offset in world units, yaw degrees)."""
        p = np.array(self.world, float) + np.asarray(offset, float)
        return StaticDriver(layers_of(effect, skill), _translate(p) @ _rot_y(yaw), start, duration, name="scene")

    def render_kwargs(self) -> dict:
        """What FxRenderer.render takes from a scene: the Light and the Post, plus the camera and frame descriptors for the
        base scene (hdkit.view.mapview.hd.render uses `frame` and `preset`)."""
        return {"light": self.light.light, "post": self.light.post.as_kwargs(), "camera": self.camera,
                "frame": self.frame, "preset": self.preset, "point_lights": self.lights}


def scene(map: str, xy, radius: float = 6.0, source: str = "auto", fs=None, roots=None, work_data=None, preset_path: str | None = None,
          subtile: bool = False, prefer: str = "day", tone: str = "lut", bloom_gain: float = 1.0, max_lights: int = 8,
          scale: float = 1.0) -> SceneContext:
    """A map spot: `xy` in cell coordinates (`subtile=True`: sub-tiles). Reads the preset, its biome's vis file (light,
    exposure, bloom: `light_preset` with the environment's lightMask) and the preset's point lights within `radius` cells
    (`mapview.hd.preset_lights`, gain `VIS_LIGHT_GAIN` as the map render uses with a vis rig). `fs` / `roots` as the
    mapview (`--root` specs); without data the generic game rig is returned with a note."""
    from hdkit.view import camera, preset as P
    from hdkit.view.mapview import hd
    from hdkit.view.mapview.frame import Frame
    cx, cy = (xy[0] / 5.0, xy[1] / 5.0) if subtile else (float(xy[0]), float(xy[1]))
    specs = hd.roots_for(source, Path(work_data) if work_data else None, roots)
    fs = fs if fs is not None else hd.fs_for(specs)
    key = str(map).lower().replace("\\", "/")
    notes = []
    logical = hd.find_preset(fs, key, source, preset_path)
    biome = vis_logical = None
    vis_bytes = None
    lights = []
    if logical is None:
        notes.append("no HD preset for %s: generic game rig, no map lights" % key)
    else:
        pj = P._read_json(fs, logical)
        biome = pj.get("biomeFilename")
        if biome:
            bp = biome.replace("\\", "/")
            bp = bp if bp.endswith(".json") else "hd/env/biome/%s.json" % bp
            try:
                names = [v.get("visualDataFilename") for v in _json(fs, bp).get("visualDataFilenames") or []
                         if v.get("visualDataFilename")]
                vis_logical = next((n for n in names if prefer in n.lower()), names[0] if names else None)
                if vis_logical:
                    vis_bytes = fs.read(vis_logical)
            except (OSError, ValueError, KeyError):
                notes.append("biome %s: vis file not read" % biome)
        else:
            notes.append("preset names no biome: generic game rig")
        vd = {}
        if vis_bytes:
            try:
                vd = next((c for e in json.loads(vis_bytes.decode("utf-8-sig")).get("entities", []) for c in e.get("components", [])
                           if c.get("type") == "VisualDataComponent"), {})
            except ValueError:
                pass
        near = []
        for L in hd.preset_lights(fs, logical, hd.VIS_LIGHT_GAIN if vis_bytes else hd.LIGHT_GAIN):
            d = math.hypot(L["pos"][0] - cx * CELL, L["pos"][2] - cy * CELL)
            if d <= radius * CELL + L["radius"]:
                near.append((d, L))
        lights = [L for _d, L in sorted(near, key=lambda t: t[0])[:max_lights]]
    lp = light_preset(vis_bytes, light_mask=hd.WORLD_MASK, tone=tone, bloom_gain=bloom_gain, name=vis_logical or "game")
    if vis_bytes:
        amb = hd.AMBIENT * float(vd.get("iblIntensity", 1.0))
        from dataclasses import replace
        lp.light = replace(lp.light, ambient=(amb,) * 3, env_col=(amb,) * 3, floor_clip=0.0, points=tuple(lp.light.points) + tuple(lights))
        lp.assumptions.append("ambient = %g x iblIntensity (calibrated on cathy3 4K captures, mapview.hd.AMBIENT)" % hd.AMBIENT)
    fr = Frame(max(0, int(math.floor(cx - radius))), max(0, int(math.floor(cy - radius))), int(math.ceil(cx + radius)),
               int(math.ceil(cy + radius)), float(scale))
    cam = {"el": camera.GAME_EL, "az": camera.GAME_AZ, "k": camera.K_1600x900 * fr.hd_scale()}
    return SceneContext(key, (cx, cy), (cx * CELL, 0.0, cy * CELL), logical, biome, vis_logical, lp, lights, fr, cam, specs, notes)


# ---- playing drivers against a Sim ----------------------------------------------------------------------------------------------

def drive(driver: Driver, make_sim, fps: float = FPS, duration: float | None = None, seed: int = 0):
    """Play `driver` and its children: `make_sim(effect, ctx, seed) -> sim` (an object with `.ctx`, `.step(dt)` and
    `.state()`; the vm lane's `Sim(effect, seed, ctx=ctx)` fits). Yields (t, [(label, SimState)]) per frame: one entry per
    layer of the driver and of every child that has started. Children (explosions, overlays, missiles of a cast) start at
    their time and run for their own duration."""
    drivers = [driver] + [c for _t, c in _all_children(driver)]
    sims: dict = {}
    end = max(d.start + d.duration for d in drivers) if duration is None else driver.start + duration
    dt, n = 1.0 / fps, int(round((end - driver.start) * fps))
    prev = {}
    for i in range(n + 1):
        t = driver.start + i * dt
        out = []
        for di, d in enumerate(drivers):
            if t < d.start - 1e-9 or t > d.start + d.duration + 1e-9:
                continue
            f = d.frame(t, prev.get(di))
            prev[di] = t
            for li, lay in enumerate(d.layers):
                k = (di, li)
                if k not in sims:
                    sims[k] = make_sim(lay.effect, f.contexts[li], seed + di * 17 + li)
                s = sims[k]
                s.ctx = f.contexts[li]
                s.step(dt if i or d.start < t else 0.0)
                out.append(("%s:%s" % (d.name, lay.label), s.state()))
        yield t, out


def _all_children(d: Driver) -> list:
    out = []
    for t, c in d.children:
        out.append((t, c))
        out += _all_children(c)
    return out


# ---- CLI ----------------------------------------------------------------------------------------------------------------------

def _fs_from(args):
    from hdkit.view import vfs
    specs = list(args.root or [])
    if not specs:
        env = os.environ.get("HDKIT_VIEW_ROOTS", "")
        specs = [s for s in env.split(os.pathsep) if s]
    if not specs:
        raise SystemExit("no game data: pass --root name=<folder> (or --root casc) or set HDKIT_VIEW_ROOTS")
    return vfs.Layered(vfs.parse_roots(specs))


def _tables_from(args):
    d = args.tables or os.environ.get("HDFX_TABLES")
    return Tables.from_dir(d) if d else None


def _fmt_binding(b: Binding) -> str:
    bits = ["%-12s %-14s %s" % ("[%s]" % b.kind, b.entity, b.file or "-")]
    if b.trigger != "entity":
        bits.append("<%s%s%s%s>" % (b.trigger, " " + b.mode if b.mode else "", " @%.3fs" % b.time if b.time is not None else "",
                                     "" if b.attach is None else (" attached" if b.attach else " free")))
    bits.append("pos %s scale %s" % (tuple(round(v, 3) for v in b.position), tuple(round(v, 3) for v in b.scale)))
    if b.bone:
        bits.append("bone %s%s" % (b.bone, " (position only)" if b.inherit_only_position else ""))
    if b.source_skill:
        bits.append("only for skill %r" % b.source_skill)
    if b.release_point:
        bits.append("follows the releaser")
    return "  " + "  ".join(bits)


def _print_missile(m: MissileEffects, indent: str = "") -> None:
    print("%smissile %s  key %s  ->  %s" % (indent, m.name, m.key, m.path or "(no HD definition)"))
    r = m.row
    if r:
        print("%s  row: Vel %g (%.2f u/s)  Accel %g  MaxVel %g  Range %g frames  Light %g rgb %s  Trans %s" % (
            indent, r["vel"], r["speed_units_s"], r["accel"], r["maxvel"], r["range"], r["light"], r["rgb"], r["trans"] or "-"))
    for b in m.bindings:
        print(indent + _fmt_binding(b))
    for c, n in m.children:
        print("%s  makes %s: %s" % (indent, c, n))
    for n in m.notes:
        print("%s  note: %s" % (indent, n))


def _print_unit(u: UnitEffects) -> None:
    print("unit %s  %s  root scale %g  skeleton %s" % (u.unit, u.path, u.root_scale, u.skeleton or "-"))
    for b in u.bindings:
        if b.kind != "model" and b.trigger != "timeline":
            print(_fmt_binding(b))
    tl = sorted(u.timeline(), key=lambda b: (b.mode, b.time or 0.0))
    for b in tl:
        print(_fmt_binding(b))
    if u.overlay_height:
        print("  overlay height %s (unverified)" % json.dumps(u.overlay_height))
    for h in u.hit:
        if h.get("table") != "success":                     # the success ones are the `hitfx` bindings above
            print("  [hit]        %s" % json.dumps(h))
    for n in u.notes:
        print("  note: %s" % n)


def _print_overlay(o: OverlayEffects, indent: str = "") -> None:
    print("%soverlay %s  key %s  ->  %s" % (indent, o.name, o.key, o.path or "(no HD definition)"))
    if o.height:
        print("%s  height %s (unverified)" % (indent, json.dumps(o.height)))
    for b in o.bindings:
        print(indent + _fmt_binding(b))
    for n in o.notes:
        print("%s  note: %s" % (indent, n))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx context", description="what the game binds an effect to")
    ap.add_argument("what", choices=["unit", "missile", "skill", "overlay", "state", "monster", "scene", "env", "global"])
    ap.add_argument("name", nargs="?", default="", help="unit / missile / skill / overlay / state / monster name or path, a map key "
                    "for scene, a vis name or path for env (global takes none)")
    ap.add_argument("xy", nargs="*", type=float, help="scene: cell x y")
    ap.add_argument("--root", action="append", help="game data root name=folder or casc (default: HDKIT_VIEW_ROOTS)")
    ap.add_argument("--tables", help="folder with missiles.txt, skills.txt, overlay.txt (default: HDFX_TABLES)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fs, tb = _fs_from(a), _tables_from(a)
    if a.what == "scene":
        if len(a.xy) < 2:
            raise SystemExit("scene needs a map key and cell x y")
        sc = scene(a.name, a.xy[:2], fs=fs)
        d = {"map": sc.map, "xy": sc.xy, "world": sc.world, "preset": sc.preset, "biome": sc.biome, "vis": sc.vis,
             "post": sc.light.post.as_dict(), "light": sc.light.light.as_dict(), "point_lights": len(sc.lights),
             "camera": sc.camera, "frame": sc.frame.as_dict(), "notes": sc.notes}
        print(json.dumps(d, indent=1, default=str))
        return 0
    if a.what in ("env", "global"):
        if a.what == "env" and not a.name:
            raise SystemExit("env needs a vis name (hd/env/vis/<name>.json) or path")
        bs = screenspace_effects(fs, a.name) if a.what == "env" else global_tables_effects(fs)
        if a.json:
            print(json.dumps([b.as_dict() for b in bs], indent=1))
        for b in ([] if a.json else bs):
            print(_fmt_binding(b) + ("  key %s" % b.extra["key"] if b.extra.get("key") else ""))
        return 0
    if a.what in ("missile", "unit", "skill", "overlay", "state", "monster", "scene") and not a.name:
        raise SystemExit("%s needs a name" % a.what)
    if a.what == "unit":
        u = effects_of_unit(fs, a.name, tb)
        if a.json:
            print(json.dumps(u.as_dict(), indent=1))
            return 0
        _print_unit(u)
        return 0
    if a.what == "monster":
        if tb is None:
            raise SystemExit("monster needs the txt tables: --tables <folder> or HDFX_TABLES")
        m = effects_of_monster(fs, a.name, tb)
        if a.json:
            print(json.dumps(m.as_dict(), indent=1))
            return 0
        if m.unit:
            _print_unit(m.unit)
        for sk in m.skills:
            print("  skill%d %s  mode %s  lvl %s%s" % (sk["slot"], sk["skill"], sk["mode"] or "-", sk["level"] or "-",
                                                      "  (%s)" % sk["note"] if sk["note"] else ""))
            for r in sk["effects"].refs if sk["effects"] else []:
                print("    %s = %s  %s" % (r.column, r.name, ", ".join(sorted({b.file.rsplit("/", 1)[-1] for b in r.effects.vfx()})) or "-"))
        for n in m.notes:
            print("  note: %s" % n)
        return 0
    if a.what == "state":
        if tb is None:
            raise SystemExit("state needs the txt tables: --tables <folder> or HDFX_TABLES")
        st = effects_of_state(fs, a.name, tb)
        if a.json:
            print(json.dumps(st.as_dict(), indent=1))
            return 0
        print("state %s" % st.name)
        for r in st.refs:
            print("  %s (%s) = %s" % (r.column, STATE_OVERLAY_COLS[r.column], r.name))
            _print_overlay(r.effects, "    ")
        for n in st.notes:
            print("  note: %s" % n)
        return 0
    if a.what == "overlay":
        o = effects_of_overlay(fs, a.name, tb)
        print(json.dumps(o.as_dict(), indent=1) if a.json else "", end="")
        if not a.json:
            _print_overlay(o)
        return 0
    if a.what == "missile":
        m = effects_of_missile(fs, a.name, tb)
        if a.json:
            print(json.dumps(m.as_dict(), indent=1))
            return 0
        _print_missile(m)
        seen = {m.key}
        for col, kid in m.children:
            if norm_key(kid) not in seen:
                seen.add(norm_key(kid))
                _print_missile(effects_of_missile(fs, kid, tb), "    (%s) " % col)
        return 0
    if tb is None:
        raise SystemExit("skill needs the txt tables: --tables <folder> or HDFX_TABLES")
    s = effects_of_skill(fs, a.name, tb)
    if a.json:
        print(json.dumps(s.as_dict(), indent=1))
        return 0
    print("skill %s  anim %s -> mode %s  monanim %s" % (s.name, s.anim or "-", s.mode, s.monanim or "-"))
    for r in s.refs:
        print("  %s = %s" % (r.column, r.name))
        (_print_missile if r.kind == "missile" else _print_overlay)(r.effects, "    ")
    for n in s.notes:
        print("  note: %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
