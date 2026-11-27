"""Scene inputs for fx/lighting.py read from a level's preset (lane hdfx-r3-lighting2; docs/hdfx.md section 3, "Scene
lighting"): the biome, the map's own lights (preset and prefab Point/SpotLightDefinitionComponents), the lights of the
map's placed effects (VfxDefinitionComponent: torches, fires, braziers, whose light renderers the game draws as point
lights) and a time of day.

    L = scene_inputs.for_preset(fs, "act1/town/townn1", at=(370.0, 120.0), radius=40.0)    # -> lighting.GameLighting
    --rig "scene:preset=act1/town/townn1;at=370,120;clut=..."                               (fx CLI, fxcast)

Checked against the game (RenderDoc calib r3, Rogue Encampment, Uber Mephisto's unit draw 24399, light records): the
preset light `accent_point01_04` (prefab of townn1: power 200, radius 25, attenuation 4, colour (1, .694, .431), 4.27
up) is the capture's record point133: colour 200 / (4 pi) x colour = (15.92, 11.05, 6.87), radius 25 as is, exponent 4,
the same place. So a preset light in the game's form is: colour = power / (4 pi) x colour x diffuseContribution,
radius = radius (spots: distance), exponent = attenuation, at the entity's world transform (prefab transforms composed).
Lights of power 0 are left out (the game's light grid lists none). The flickering torch lights of the same capture
(exponent 1.6, radius 22-26) come from placed effects' light renderers, not from the preset (`vfx_lights`).

Time of day: every calib bench (new games, the first minutes) shows one blend, 0.53-0.55 of the way from dawn2 to day
(lane r3-lighting: the captured key light's power, colour and direction), so NEW_GAME_TOD = 1.54 is the default for a
scene a fresh game shows. The game's clock rate was not measured (all benches ran within minutes of a new game).
"""
from __future__ import annotations

import json
import math

import numpy as np

NEW_GAME_TOD = 1.54           # dawn2 -> day at 0.54: every calib bench (fresh games, first minutes)
CELL = 10.0                   # world units per cell (preset coordinates are world units)


def _read(fs, logical):
    return json.loads(fs.read(logical).decode("utf-8-sig"))


def _norm_preset(fs, key: str) -> str:
    """A preset key ('act1/town/townn1', a .ds1 path from lvlprest, or a logical .json path) -> its logical path."""
    k = str(key).replace("\\", "/").lower()
    if k.endswith(".ds1"):
        k = k[:-4]
    if k.endswith(".json"):
        return k
    for c in ("hd/env/preset/%s.json" % k, "data/hd/env/preset/%s.json" % k):
        if fs.exists(c):
            return c
    return "hd/env/preset/%s.json" % k


def walk(fs, preset: str, prefabs: bool = True):
    """Yields (entity name, components by type, 4x4 world transform) over a preset and its prefabs (transforms composed,
    view.preset.trs)."""
    from ..view import preset as P

    def go(doc, M0, tag, depth):
        for e in doc.get("entities", []):
            c = {x.get("type"): x for x in e.get("components", [])}
            M = M0 @ P.trs(c.get("TransformDefinitionComponent"))
            yield tag + e.get("name", ""), c, M
            pf = c.get("PrefabPlacementDefinitionComponent")
            if pf and prefabs and depth < 4:
                try:
                    sub = _read(fs, pf["prefab"])
                except (OSError, KeyError, ValueError, FileNotFoundError):
                    continue
                yield from go(sub, M, tag + e.get("name", "") + "/", depth + 1)

    yield from go(_read(fs, preset), np.eye(4), "", 0)


def light_from_component(name, comp: dict, M, spot: bool = False) -> dict | None:
    """A preset Point/SpotLightDefinitionComponent at world transform M in the game's light form (lighting.py `extra`:
    {name, pos, color, radius, exp, diffuse, specular[, spot]}); None for a light of power 0."""
    pw = float(comp.get("power", 0.0))
    if pw <= 0.0:
        return None
    col = comp.get("color") or {}
    k = pw / (4.0 * math.pi) * float(comp.get("diffuseContribution", 1.0))
    out = {"name": name, "pos": [round(float(v), 5) for v in np.asarray(M, float)[:3, 3]],
           "color": [k * float(col.get(c, 1.0)) for c in "xyz"],
           "radius": float(comp.get("distance" if spot else "radius", 20.0 if spot else 10.0)),
           "exp": float(comp.get("attenuation", 2.0)), "diffuse": 1.0,
           "specular": float(comp.get("specularContribution", 1.0)), "source": "preset"}
    if spot:
        from ..view import shadow as S
        out["spot"] = S.spot_from_preset(comp, M)["spot"]      # +Z axis, cone, shadow index (view/shadow.py)
    return out


def preset_lights(fs, preset: str, near=None, radius: float | None = None) -> list:
    """The preset's own lights (and its prefabs') in the game's form, nearest first; `near` = (x, z) world and `radius`
    (world units, plus each light's own radius) keep the ones that can reach there."""
    out = []
    for name, c, M in walk(fs, _norm_preset(fs, preset)):
        for t, spot in (("PointLightDefinitionComponent", False), ("SpotLightDefinitionComponent", True)):
            if t in c:
                L = light_from_component(name, c[t], M, spot)
                if L is not None:
                    out.append(L)
    return _near(out, near, radius)


def _near(lights, near, radius):
    if near is None:
        return lights
    x, z = float(near[0]), float(near[1])
    d = [(math.hypot(L["pos"][0] - x, L["pos"][2] - z), L) for L in lights]
    if radius is not None:
        d = [(r, L) for r, L in d if r <= radius + L["radius"]]
    return [L for _r, L in sorted(d, key=lambda t: t[0])]


def preset_vfx(fs, preset: str, near=None, radius: float | None = None) -> list:
    """[(name, effect logical path, 4x4 transform)] of the preset's placed effects (VfxDefinitionComponent)."""
    out = []
    for name, c, M in walk(fs, _norm_preset(fs, preset)):
        v = c.get("VfxDefinitionComponent")
        f = v and (v.get("filename") or v.get("vfxFilename") or v.get("file"))
        if f:
            out.append((name, f, M))
    if near is not None:
        x, z = float(near[0]), float(near[1])
        r = 1e9 if radius is None else radius + 30.0            # a placed effect's light reaches ~30 units at most
        out = [o for o in out if math.hypot(o[2][0, 3] - x, o[2][2, 3] - z) <= r]
    return out


def vfx_lights(fs, path: str, M, t: float = 1.0, seed: int = 0) -> list:
    """The point lights a placed effect's light renderers give at time `t` (s): lane vm's Sim at the transform M, its
    SimState.lights in the game's form (packers.lights_from, as fx/render.py draws them), as lighting.py `extra`
    dicts. [] when the effect has no light renderer or does not run."""
    from ..view import fxcli
    from . import packers as pk, rspec
    data = fs.read(path)
    try:
        eff = rspec.load(data)
    except Exception:                       # noqa: BLE001 - a broken effect gives no light
        return []
    if not any(r.kind == "light" for r in eff.renderers):
        return []
    steep = {r.index: float(r.get("LightAttenuation.AttenuationSteepness", 1.0) or 1.0) for r in eff.renderers}
    try:
        sim = fxcli.make_sim(data, seed, np.asarray(M, np.float32))
        sim.seek(float(t))
        st = sim.state()
    except Exception:                       # noqa: BLE001
        return []
    out = []
    for L in getattr(st, "lights", None) or []:
        pos = np.asarray(L.get("position", np.zeros((0, 3))), float).reshape(-1, 3)
        n = len(pos)
        if not n:
            continue
        on = np.asarray(L.get("on", np.ones(n)), bool).reshape(-1)
        on = on if len(on) == n else np.ones(n, bool)
        col = np.asarray(L.get("color", np.ones((n, 4))), float).reshape(n, -1)
        col = np.hstack([col[:, :3], np.ones((n, 1))])
        power = np.asarray(L.get("power", np.ones(n)), float).reshape(n)
        rng = np.asarray(L.get("range", np.full(n, 5.0)), float).reshape(n)
        for f in pk.lights_from(pos[on], col[on], rng[on], power[on], steep.get(L.get("renderer"), 1.0)):
            out.append({"name": path.rsplit("/", 1)[-1], "pos": [float(v) for v in f.pos],
                        "color": [float(v) for v in f.color], "radius": float(f.radius), "exp": float(f.atten),
                        "diffuse": 1.0, "specular": 1.0, "source": "vfx"})
    return out


def object_lights(fs, objects, t: float = 1.0) -> list:
    """Lights of placed HD objects: `objects` = [(object json, (x, y, z) world[, yaw])] (data/hd/objects/...json, e.g.
    the campfire or a brazier). Each object's particle dependencies are run at its place (vfx_lights): the game's
    object lights are those effects' light renderers (the Rogue Encampment torches of calib r3: exponent 1.6, radius
    22-26, flickering). Objects whose effects have no light renderer give none."""
    out = []
    for o in objects or []:
        path, pos = o[0], o[1]
        yaw = float(o[2]) if len(o) > 2 else 0.0
        try:
            deps = (_read(fs, path).get("dependencies") or {}).get("particles") or []
        except (OSError, ValueError, FileNotFoundError):
            continue
        c, s_ = math.cos(yaw), math.sin(yaw)
        M = np.array([[c, 0, s_, pos[0]], [0, 1, 0, pos[1]], [-s_, 0, c, pos[2]], [0, 0, 0, 1]], float)
        for d in deps:
            for L in vfx_lights(fs, d["path"], M, t):
                out.append(dict(L, source="object", object=path))
    return out


def biome_of(fs, preset: str) -> str | None:
    b = _read(fs, _norm_preset(fs, preset)).get("biomeFilename")
    if not b:
        return None
    return b.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".json")


def scene_lights(fs, preset: str, at, radius: float = 40.0, fx_lights: bool = True, t: float = 1.0,
                 max_lights: int = 16, objects=None) -> tuple:
    """(lights, notes): the preset's lights, its placed effects' lights and the `objects`' lights (object_lights) that
    reach `at` = (x, z) world, nearest first, at most `max_lights`."""
    lights = preset_lights(fs, preset, at, radius)
    notes = ["%d preset light(s) within %g units" % (len(lights), radius)]
    if objects:
        ol = _near(object_lights(fs, objects, t), at, radius)
        notes.append("%d object light(s)" % len(ol))
        lights += ol
    if fx_lights:
        n = 0
        for name, path, M in preset_vfx(fs, preset, at, radius):
            ls = vfx_lights(fs, path, M, t)
            n += len(ls)
            lights += ls
        lights = _near(lights, at, radius)
        notes.append("%d light(s) from placed effects at t = %g s" % (n, t))
    return lights[:max_lights], notes


def for_preset(fs, preset: str, at=(0.0, 0.0), radius: float = 40.0, tod=None, player=None, fx_lights: bool = True,
               t: float = 1.0, objects=None, **kw):
    """lighting.GameLighting of a map spot `at` (x, z preset world units), which becomes the scene's origin (where the
    viewer places the effect and the player): the preset's biome, time of day `tod` (None: NEW_GAME_TOD), the player's
    feet at the origin (`player`: (x, y, z) relative to it), the map's lights reaching there as `extra`. Keyword
    arguments go to lighting.for_biome (player_on_plate, shadows, table)."""
    from . import lighting
    biome = biome_of(fs, preset)
    if biome is None:
        raise ValueError("preset %s names no biome" % preset)
    lights, notes = scene_lights(fs, preset, at, radius, fx_lights, t, objects=objects)
    o = np.array([float(at[0]), 0.0, float(at[1])])
    lights = [dict(x, pos=(np.asarray(x["pos"], float) - o).round(5).tolist()) for x in lights]   # the spot = origin
    p = tuple(player) if player is not None else (0.0, 0.0, 0.0)
    L = lighting.for_biome(fs, biome, NEW_GAME_TOD if tod is None else tod, p, lights, **kw)
    L.sources.update({"preset": _norm_preset(fs, preset), "at": [float(at[0]), float(at[1])], "radius": radius})
    L.assumptions += notes
    return L
