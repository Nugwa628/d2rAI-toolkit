"""Open-ground fire layouts based on the server skill and missile functions (D2MOO)."""
from __future__ import annotations

import math

import numpy as np

from . import context as C, wallcast


def source(skill):
    row = skill.row or {}
    blaze = C._num(row.get("srvdofunc")) == 23
    column = "srvmissilea" if blaze else "srvmissile"
    ref = next((r for r in skill.refs if r.kind == "missile" and column in r.column.split(",") and r.effects), None)
    if ref is None:
        return None, None
    facts = ref.effects.row or {}
    if blaze and facts.get("server_hit_func") == 8:
        return "blaze", ref.effects
    if facts.get("server_hit_func") == 9:
        return "immolation_arrow", ref.effects
    if facts.get("server_do_func") == 6 or facts.get("server_hit_func") == 48:
        return "molten_boulder", ref.effects
    return None, None


def resolve(skill, fs, tables):
    """Bind the server-side children used by the fire layouts."""
    kind, parent = source(skill)
    if kind == "blaze":
        stats = tables.row("charstats", "Sorceress") or {}
        skill.run_velocity = C._num(stats.get("runvelocity"), skill.run_velocity)
    elif kind in ("molten_boulder", "immolation_arrow"):
        while parent:
            facts = parent.row
            link = "submissile" if facts.get("server_do_func") == 6 else "hit_submissile"
            name = facts.get(link)
            if not name or name in skill.fire_missiles:
                break
            child = C.effects_of_missile(fs, name, tables)
            skill.fire_missiles[name] = child
            parent = child if facts.get("server_hit_func") == 48 else None


def children(skill, toward, level, launch, place, origin, mode_len):
    kind, parent = source(skill)
    if parent is None:
        return None
    distance = math.hypot(*toward)
    direction = np.asarray(toward, float) / (distance or 1.0)
    sx, sy = origin

    def world(x, y):
        return (place @ np.array([C.UNITS_PER_SUBTILE * (x - sx), 0, C.UNITS_PER_SUBTILE * (y - sy), 1]))[:3]

    out, info, drops = [], {"missile_func": kind, "missiles": [], "count": 0}, []

    def projectile(effect, frame, start, end, speed, life):
        when = launch + frame / C.FPS
        row = dict(effect.row, speed_units_s=speed * C.FPS * C.UNITS_PER_SUBTILE)
        drv = C.missile(effect, path=[world(*start), world(*end)], row=row, range_frames=life, start_time=when,
                        skill=skill.name)
        drv.name = "missile:%s#0" % effect.name
        out.append((when, drv))
        info["missiles"].append({"name": effect.name, "index": 0, "frame": frame, "at_subtile": list(start),
                                 "range_frames": life, "t_end": round(drv.t_end, 4)})
        return drv

    def velocity(effect):
        row = effect.row
        vel = row.get("vel", 0) + int(level * row.get("vellev", 0) / 8)
        return vel * C.VEL_SUBTILES_PER_FRAME

    def trail(start, frames, speed, delay, by, limit=None):
        # Missile/unit paths start at a sub-tile centre, following the Fire Wall convention.
        return [{"frame": delay + k, "at": at, "dir": direction, "by": by}
                for k, at in wallcast.maker_drops(direction, frames, speed, np.asarray(start) + 0.5, limit)]

    if kind == "blaze":
        # SrvDo023 applies the state. Player movement emits srvmissilea on each entered sub-tile.
        row = skill.row or {}
        if row.get("aurastate", "").lower() != "blaze":
            return out, info
        life = int(C.skill_calc(row.get("auralencalc") or "0", row, level))
        speed = skill.run_velocity / 16.0   # player path velocity has no missile 75% penalty
        frames = math.ceil(distance / speed) if speed > 0 else 0
        delay = max(0, math.ceil((mode_len - launch) * C.FPS - 1e-9))
        drops = trail(origin, min(frames, max(0, life - delay - 1)), speed, delay, "movement", distance)
        fire, override = parent, None
    elif kind == "molten_boulder":
        # SrvHit48 starts the rolling missile on expiry. SrvDo06 drops SubMissile1 as it enters a sub-tile.
        frame, start = 0, np.asarray(origin, float)
        if parent.row.get("server_hit_func") == 48:
            frame = int(wallcast.range_of(parent.row, level))
            speed = velocity(parent)
            end = start + direction * speed * frame
            projectile(parent, 0, start, end, speed, frame)
            start = np.floor(end + 0.5)
            parent = skill.fire_missiles.get(parent.row.get("hit_submissile"))
        if parent is None or parent.row.get("server_do_func") != 6 or not distance:
            return out, info
        frames, speed = int(wallcast.range_of(parent.row, level)), velocity(parent)
        projectile(parent, frame, start, start + direction * speed * frames, speed, frames)
        drops = trail(start, frames - 1, speed, frame, parent.name)
        fire, override = skill.fire_missiles.get(parent.row.get("submissile")), None
    else:
        # SrvHit09 drops HitSubMissile1 on every integer cell of the impact disc.
        life, speed = int(wallcast.range_of(parent.row, level)), velocity(parent)
        target = np.floor(np.asarray(toward) + 0.5)
        # Aim at the clicked sub-tile's centre and hit on the update that first reaches its boundary.
        direction = target / (math.hypot(*target) or 1.0)
        impact = life
        if speed > 0:
            crossings = ((x, (abs(x) - 0.5) / abs(u * speed)) for x, u in zip(target, direction) if u)
            # The upper edge of a sub-tile is exclusive when moving in a negative direction.
            impact = max([1] + [math.floor(t) + 1 if x < 0 else math.ceil(t) for x, t in crossings])
        frame = min(life, impact)
        end = np.asarray(origin) + direction * speed * frame
        projectile(parent, 0, origin, end, speed, life)
        cell = target if impact <= life else np.floor(direction * speed * life + 0.5)
        x, y = map(int, np.asarray(origin) + cell)
        row = skill.row or {}
        radius = parent.row.get("hit_radius", 0)
        if radius <= 0:
            radius = max(1, int(C.skill_calc(row.get("calc1") or "0", row, level)))
        drops = [{"frame": frame, "at": (x + i, y + j), "dir": direction, "by": parent.name}
                 for i in range(-radius, radius + 1) for j in range(-radius, radius + 1) if i*i + j*j <= radius*radius]
        fire = skill.fire_missiles.get(parent.row.get("hit_submissile"))
        override = max(0, int(C.skill_calc(parent.row.get("hit_calc") or "0", None, level)))
    if fire is not None:
        ch, mi = wallcast.fire_children(fire, skill.name, level, launch, world, drops, override)
        out.extend(ch)
        info["missiles"].extend(mi)
        info["count"] = len(ch)
    return out, info
