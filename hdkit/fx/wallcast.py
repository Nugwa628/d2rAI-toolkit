"""Fire Wall's missiles as the game lays them (lane hdfx-r4-firewall).

The game's Fire Wall is not one effect: it is a line of `firewall` missiles, each playing its own copy of
`vfx_ground_fire_firewall.particles`. Each copy rolls its own `EventRandPick` (large / medium / small fire card; the
large branch also spawns `largeFireCylinder`), so a single seed-0 copy at the caster, as the viewer drew before, only ever
showed the small card and the flat disc. In the game capture of a Fire Wall cast (lane r3-integrate g1, 0.8 s) the disc
mesh draw has 8 instances and the cylinder draw 3: 8 copies, about a third of them on the large branch.

Read from the game code as decompiled by D2MOO (github.com/ThePhrozenKeep/D2MOO, MIT, credited in THIRD_PARTY.md):
- `SKILLS_SrvDo024_FireWall` (D2Game SkillSor.cpp): at the target sub-tile T, two `srvmissilea` missiles (firewallmaker)
  aimed at T + (dy, -dx) and T + (-dy, dx), where (dx, dy) = T - caster, i.e. along both ways perpendicular to the cast;
  then one `srvmissileb` (firewall) on T itself.
- `MISSMODE_SrvDo06_MoltenBoulder_FireWallMaker` (D2Game MissMode.cpp): every update in which the maker's path entered a
  new sub-tile (`D2Common_10234`, path flag 0x8) makes its SubMissile1 (firewall) on the maker's sub-tile.
- `MISSILES_CreateMissileFromParams` (D2Game Missiles.cpp): range = Range + skill level x LevRange frames.

Checked against the game: the server track of lane r3-integrate's g2 cast (Fire Wall level 1 aimed 6 sub-tiles along +x;
`firewall` missiles, class 69) has exactly this layout: 10 fires at T + (0, 0), (0, +-1 .. +-4), (0, +5).

Assumptions (not read): the maker starts at the centre of T and makes no fire on its first update unless it already
crossed a sub-tile edge; each fire's effect faces the maker's direction (the game's fire missile has no direction of its
own; the effect is round and its cards are billboards, so this only turns the disc's texture).
"""
from __future__ import annotations

import math

import numpy as np

SRVDOFUNC = 24          # skills.txt srvdofunc of Fire Wall


def maker_drops(direction, frames: int, vel_subtiles: float, start=(0.5, 0.5), distance=None) -> list:
    """[(frame, (x, y))]: the sub-tiles a maker leaving `start` (sub-tile units, relative to the target) along `direction`
    at `vel_subtiles` per frame enters, and the update (1..frames) that enters each."""
    d = np.asarray(direction, float)
    n = float(np.hypot(*d))
    if n < 1e-9 or frames <= 0 or vel_subtiles <= 0:
        return []
    u = d / n
    p = np.asarray(start, float)
    origin = p
    cur = (math.floor(p[0]), math.floor(p[1]))
    out = []
    for k in range(1, int(frames) + 1):
        p = p + u * vel_subtiles if distance is None else origin + u * min(k * vel_subtiles, distance)
        st = (math.floor(p[0]), math.floor(p[1]))
        if st != cur:
            out.append((k, st))
            cur = st
    return out


def range_of(facts: dict, level: int) -> float:
    """A missile's life in frames at skill `level`: Range + level x LevRange (D2MOO MISSILES_CreateMissileFromParams). A
    Range written as a formula (the mod's tables: `7+2*lvl`) is evaluated with `lvl` = level instead."""
    from . import context as C
    expr = str(facts.get("range_expr", "") or "")
    try:
        float(expr or "0")
    except ValueError:
        try:
            return float(C.skill_calc(expr, None, level))
        except ValueError:
            pass
    return float(facts.get("range", 0.0) or 0.0) + level * float(facts.get("levrange", 0.0) or 0.0)


def layout(toward, maker_range: float, maker_levrange: float, maker_vel: float, level: int) -> dict:
    """Where and when (game frames after the cast) Fire Wall's fires appear, the caster at (0, 0) and the target at
    `toward` (sub-tiles). {"makers": [{"dir", "frames"}], "fires": [{"frame", "at", "dir", "by"}]}."""
    from . import context as C
    tx, ty = (float(v) for v in toward)
    T = (math.floor(tx), math.floor(ty))
    # the maker lives Range + level x LevRange frames and moves one update less (its counter starts at the range and it
    # dies at 0): FITS the server track of the g2 cast (level 1, 9 frames, 10 fires at T + (0, -4..+5))
    frames = max(int(maker_range + level * maker_levrange) - 1, 0)
    v = maker_vel * C.VEL_SUBTILES_PER_FRAME
    dirs = [(ty, -tx), (-ty, tx)]
    fires, makers = [], []
    for i, d in enumerate(dirs):
        makers.append({"dir": d, "frames": frames, "vel_subtiles": v})
        for k, (x, y) in maker_drops(d, frames, v):
            fires.append({"frame": k, "at": (T[0] + x, T[1] + y), "dir": d, "by": "maker%d" % i})
    fires.append({"frame": 0, "at": T, "dir": dirs[0], "by": "srvmissileb"})
    fires.sort(key=lambda f: (f["frame"], f["by"]))
    return {"target": T, "makers": makers, "fires": fires}


def children(skill_fx, toward, level: int, tt: float, P, start_subtile=(0, 0)):
    """(children [(t, driver)], info) of a Fire Wall cast whose missiles leave at driver time `tt`: the two makers (their
    own HD effect, flying) and one static `firewall` missile per fire, each its own effect copy (MissileIndex = its number)."""
    from . import context as C
    maker = next((r for r in skill_fx.refs if r.kind == "missile" and "srvmissilea" in r.column.split(",")), None)
    fire = next((r for r in skill_fx.refs if r.kind == "missile" and "srvmissileb" in r.column.split(",")), None)
    if maker is None or fire is None or maker.effects is None or fire.effects is None:
        return [], {"missile_func": "firewall", "note": "srvmissilea / srvmissileb missing"}
    mrow = dict(maker.effects.row or {})
    mrange, mlev = range_of(mrow, level), 0.0
    lay = layout(toward, mrange, mlev, mrow.get("vel", 0.0), level)
    sx, sy = start_subtile
    P = np.eye(4) if P is None else np.asarray(P, float)

    def world(x, y):
        return (P @ np.array([C.UNITS_PER_SUBTILE * x, 0.0, C.UNITS_PER_SUBTILE * y, 1.0]))[:3]

    out, info = [], {"missile_func": "firewall", "target_subtile": list(lay["target"]), "missiles": [], "count": 0}
    T = lay["target"]
    for i, m in enumerate(lay["makers"]):
        u = np.asarray(m["dir"], float) / max(float(np.hypot(*m["dir"])), 1e-9)
        far = m["vel_subtiles"] * m["frames"] * 1.5 + 1.0
        a = (T[0] - sx, T[1] - sy)                      # sub-tile coordinates name the sub-tile's centre
        path = [world(*a), world(a[0] + u[0] * far, a[1] + u[1] * far)]
        dm = C.missile(maker.effects, path=path, row=mrow, range_frames=m["frames"], start_time=tt, skill=skill_fx.name)
        dm.name, dm.index = "missile:%s#%d" % (maker.name, i), i
        out.append((tt, dm))
        info["missiles"].append({"name": maker.name, "index": i, "dir": list(m["dir"]), "range_frames": m["frames"]})
    drops, details = fire_children(fire.effects, skill_fx.name, level, tt,
                                   lambda x, y: world(x - sx, y - sy), lay["fires"])
    out.extend(drops)
    info["missiles"].extend(details)
    info["count"] = len(drops)
    return out, info


def fire_children(fire, skill, level, launch, world, drops, frames=None):
    """Place one stationary missile per drop, carrying its own index and lifetime."""
    from . import context as C
    row = fire.row or {}
    rng = frames or range_of(row, level)
    out, info = [], []
    for j, f in enumerate(drops):
        t0 = launch + f["frame"] / C.FPS
        x, y = f["at"]
        u = np.asarray(f["dir"], float) / max(float(np.hypot(*f["dir"])), 1e-9)
        path = [world(x, y), world(x + u[0] * 1e-3, y + u[1] * 1e-3)]
        df = C.missile(fire, path=path, speed=0.0, row=row, range_frames=rng or None, start_time=t0, skill=skill)
        df.name, df.index = "missile:%s#%d" % (fire.name, j), j
        out.append((t0, df))
        info.append({"name": fire.name, "index": j, "frame": f["frame"], "at_subtile": list(f["at"]),
                     "by": f["by"], "range_frames": rng})
    return out, info
