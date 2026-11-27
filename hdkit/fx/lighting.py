"""One lighting setup for every scene, from the game's own data (lane hdfx-r3-lighting; docs/hdfx.md section 3 "Scene
lighting"). It replaces the per-scene fits (PLATE_IRRADIANCE from Fire Bolt's pool x1.5, a rig per capture).

    L = lighting.for_biome(fs, "act1_outdoors", tod=1.54, player=(0, 0, 0))
    L.rig(mask=19, clut=path, unit=(7, (212, 212, 255)))     # view.rig.Rig: units through game_shade, effects' lit materials
    L.plate_gain(P, fx_points)                               # a game shot relit by an effect's lights (render.plate_gain)
    --rig "scene:act1_outdoors;tod=1.54;mask=19;unit=7,212,212,255;player=-10,0,12;clut=..."   (fx CLI, fxcast)

Where every number comes from (each checked against RenderDoc captures of the game: calib3 g4 terrain draw 15460, calib4
cb_g7 terrain draws, calib r3 unit draw 27583, calib5 s3 liquid draws 8331 / 9421):
- the biome (`data/hd/env/biome/<b>.json`): its six vis files (dawn1, dawn2, day, dusk1, dusk2, night) and its IBL cube.
- time of day `tod` in [0, 6): the game blends vis file floor(tod) with the next one by frac(tod), numbers and colours
  linearly and light orientations by slerp (the capture's key light: power, colour and direction give one blend weight,
  0.53-0.55 between dawn2 and day on every calib bench; pitch stays 47.5 deg as both quaternions have it).
- directional lights: the vis DirectionalLightDefinitionComponents, colour x power, by lightMask (terrain: bit 1, global
  key 9; a model: its ModelDefinitionComponent lightMask, 19 for monsters, 68 the player): Blood Moor key (1, .90, .69) x
  9.51 equals the capture to 3 digits, Mephisto's three (sun, monster_fill x 30, monster_back x 4) too.
- ambient: SH irradiance of the cube's top mip (view/ibl.py, table ibl_sh.json, exact to 0.004 on two biomes) x the active
  vis iblIntensity (0.4 outdoors at dawn2/day, 2.0 in the Pit, 1.0 during DenOfEvil; captures' CBV 6 float 0).
- exposure 0.18 / (2^EV x 10/65) from exposureValue (EV 4: 0.073125, every capture).
- the player's light: playerUnitLightPower / (4 pi) grey (2000 outdoors: 159.15, 2125 caves: 169.10, both as captured),
  radius 26 x playerUnitLightDistanceScaler (34.658), exponent playerUnitLightAttenuation (5.5), diffuse / specular
  playerUnitLightContributions (1, 0.666), 8.0 units above the player's feet (measured: Blood Moor, the Pit and Rogue
  Encampment captures; PLAYER_LIGHT_HEIGHT).
- a unit's own light (monstats2 Light, light-r/g/b): rgb / 255 x unitLightPower / (4 pi), exponent unitLightAttenuation,
  radius 2 x Light x unitLightDistanceScaler, height radius x unitLightDistanceOffsetRatio (the Pit's skeletons, Light 2
  cyan: radius 6, (0, 39.79, 39.79); Uber Mephisto: radius 21, 10.5 up, (33.08, 33.08, 39.79), i.e. Light 7 where
  monstats2 "mephisto" says 3: pass the light the unit really has).
- the unit value curve: the cubic through (0, 0), characterContrastPoints and (1, 1), strength characterBrightnessFactor.
- terrain / units sum: albedo / pi x (sum of lights x N.L + iblIntensity x SH(n)) (terrain forward shader 3636 end: the
  same occlusion and 1 / pi on both), so a shot's ground was lit with E(P) = those terms and an effect light adds E_fx:
  shot x (1 + E_fx / E(P)) (plate_gain). The player's light is a shadowed point light 8 units up; the terrain always
  runs that loop (cb7[80] = 19 in all 29 captured terrain draws), x the shadow G (`player_on_plate` None = with the
  shadow terms). Inside levels (caves; lane r5-playerlight) its record has cube index 0, so objects shadow it on the
  ground (`cube_term`), and the ambient is x the light radius (`light_radius`, the Global Attenuation surface).
  Not in E: map lights of the preset (`extra`), object lights.
"""
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

TOD_NAMES = ("dawn1", "dawn2", "day", "dusk1", "dusk2", "night")
TERRAIN_MASK = 1              # lightMask bit the ground answers to (global key 9 has it; fill/back 1)
MONSTER_MASK = 19
PLAYER_MASK = 68
PLAYER_LIGHT_RADIUS = 26.0    # units before playerUnitLightDistanceScaler (34.658 / 1.333, every capture)
PLAYER_LIGHT_HEIGHT = 8.0     # above the player's feet (captures: Blood Moor g4/g7, the Pit s3, Rogue Encampment r3)
UNIT_KEY_BIT = 64             # lightMask bit of the units' key lights (player_key 68, monster_fill 74): shadow index 1
TABLE = Path(__file__).with_name("ibl_sh.json")
SH_TERMS = ("1", "y", "z", "x", "xy", "yz", "3z2-1", "xz", "x2-y2")


INSIDE_BIOMES = ("cave",)     # inside-level lighting by default (act1_caves: calib5 s3 x2, r3-calibfix r4 x2, r4-water)
UNIQUE_LIGHT_BONUS = 4        # monumod "light" (row 3): every unique / boss monster's light radius + 4 (capture r3)


def monster_light(row: dict, unique: bool = False) -> tuple:
    """(Light, (r, g, b)) of a monster for GameLighting.unit_light from its monstats2 row (Light, light-r/g/b). A
    unique or boss monster also has the unique modifiers that monumod.txt gives every unique without a pick weight
    (rndname, hpmultiply, light, leveladd); "light" adds UNIQUE_LIGHT_BONUS to the radius. Capture r3 (Rogue
    Encampment): Uber Mephisto, monstats2 "mephisto" Light 3, colour (212, 212, 255), has the light record radius 21 =
    2 x 7 x 1.5 with that colour; the Pit's plain skeletons (Light 2) have radius 6 (no bonus). The +4 is read from
    this one unique; D2's own value for the umod was not found in a source."""
    lt = float(row.get("Light") or 0) + (UNIQUE_LIGHT_BONUS if unique else 0)
    rgb = tuple(float(row.get(k) or 255) for k in ("light-r", "light-g", "light-b"))
    return lt, rgb


def shadow_index(light: dict) -> int:
    """The shadow index the game gives a vis directional light (light record word 11; capture s2 unit draw 13030 and r3):
    shadowCaster -> 0 (the world slice, the key light), the units' key light (lightMask bit 64: player_key_light,
    monster_fill_light) -> 1 (the units slice), every other light -> -1 (none)."""
    if light.get("shadow"):
        return 0
    return 1 if int(light.get("mask", 0)) & UNIT_KEY_BIT else -1


def cube_term(visibility, shape):
    """The shadowed point's cube visibility (ground_vis channel 3; 1 without it)."""
    v = None if visibility is None or isinstance(visibility, dict) else np.asarray(visibility, float)
    return v[..., 3] if v is not None and v.ndim == len(shape) + 1 and v.shape[-1] == 4 else np.ones(shape)


def light_radius(points, P, exposure, game_units=True) -> np.ndarray:
    """The game's Global Attenuation surface at world points P (shader "Global Attenuation", r3-shadows2 1315):
    smoothstep(clamp(exposure x sum of the local lights' max(rgb) x falloff(distance))), D2's light radius. Inside
    levels the terrain multiplies its ambient by it (cb6[1] = 1 in every cave capture, 0 outdoors). `game_units`
    False: FxLight point dicts (colour / pi, atten -e)."""
    from . import packers as pk
    P = np.asarray(P, float)
    s = np.zeros(P.shape[:-1])
    for p in points:
        if p.get("directional"):
            continue
        dv = np.asarray(p["pos"], float) - P
        d = np.sqrt((dv * dv).sum(-1))
        e = -abs(float(p["exp"])) if "exp" in p else float(p.get("atten", 1.0))
        k = 1.0 if game_units else math.pi
        s += max(p["color"]) * k * pk.light_falloff(d, float(p["radius"]), e)
    x = np.clip(s * exposure, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def shadow_terms(visibility, shape):
    """(G, [s0, s1]) from a `visibility` argument: None -> no shadow; an (..., 3) array (shadow.ShadowMaps.ground_vis:
    G, slice 0, slice 1) or a dict {"G": .., "s": [s0, s1]} -> the game's terms; any other array -> the old single
    factor on the directional lights (G 1, s0 = s1 = it)."""
    one = np.ones(shape)
    if visibility is None:
        return one, [one, one]
    if isinstance(visibility, dict):
        s = list(visibility.get("s") or [one, one]) + [one, one]
        return (np.broadcast_to(np.asarray(visibility.get("G", 1.0), float), shape),
                [np.broadcast_to(np.asarray(x, float), shape) for x in s[:2]])
    v = np.asarray(visibility, float)
    if v.ndim == len(shape) + 1 and v.shape[-1] in (3, 4):
        return v[..., 0], [v[..., 1], v[..., 2]]
    v = np.broadcast_to(v, shape)
    return one, [v, v]


def exposure_for_ev(ev: float) -> float:
    return 0.18 / (2.0 ** float(ev) * 10.0 / 65.0)


# ---- vis files and their blend --------------------------------------------------------------------------------------

def _q(d) -> np.ndarray:
    d = d or {}
    return np.array([float(d.get(k, 0.0 if k != "w" else 1.0)) for k in "xyzw"])


def slerp(a, b, t: float) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    c = float(np.dot(a, b))
    if c < 0:
        b, c = -b, -c
    if c > 0.9995:
        q = a + t * (b - a)
        return q / np.linalg.norm(q)
    th = math.acos(c)
    return (math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th)


def towards(quat) -> np.ndarray:
    """World direction towards a vis directional light: its orientation applied to -z (view.rig.light_towards)."""
    x, y, z, w = (float(v) for v in quat)
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return R @ np.array([0.0, 0.0, -1.0])


def parse_vis(doc) -> dict:
    """{"visual": VisualDataComponent, "lights": {name: {color, power, mask, quat, diffuse, specular, shadow}}}."""
    if isinstance(doc, (bytes, bytearray)):
        doc = json.loads(bytes(doc).decode("utf-8-sig"))
    vd, lights = {}, {}
    for e in doc.get("entities", []):
        comps = {c.get("type"): c for c in e.get("components", [])}
        if "VisualDataComponent" in comps:
            vd = comps["VisualDataComponent"]
        dl = comps.get("DirectionalLightDefinitionComponent")
        if dl:
            c = dl.get("color") or {}
            lights[e.get("name")] = {
                "color": [float(c.get(k, 1.0)) for k in "xyz"], "power": float(dl.get("power", 0.0)),
                "mask": int(dl.get("lightMask") or 0), "quat": _q((comps.get("TransformDefinitionComponent") or {})
                                                                 .get("orientation")).tolist(),
                "diffuse": float(dl.get("diffuseContribution", 1.0)), "specular": float(dl.get("specularContribution", 1.0)),
                "shadow": bool(dl.get("shadowCaster", False))}
    return {"visual": vd, "lights": lights}


def _lerp(a, b, t):
    if isinstance(a, bool) or isinstance(b, bool):
        return a if t < 0.5 else b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) + (float(b) - float(a)) * t
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _lerp(a[k], b[k], t) if k in b else a[k] for k in a}
    return a if t < 0.5 else b


def blend(va: dict, vb: dict, t: float) -> dict:
    """Two parsed vis files blended by t (numbers and colours linear, orientations slerp, flags and names nearest)."""
    vis = _lerp(va["visual"], vb["visual"], t)
    lights = {}
    for name, a in va["lights"].items():
        b = vb["lights"].get(name, a)
        L = {k: _lerp(a[k], b[k], t) for k in ("color", "power", "diffuse", "specular")}
        L["color"] = [float(x) + (float(y) - float(x)) * t for x, y in zip(a["color"], b["color"])]
        L.update(mask=a["mask"], shadow=a["shadow"], quat=slerp(a["quat"], b["quat"], t).tolist())
        lights[name] = L
    return {"visual": vis, "lights": lights}


def tod_index(tod) -> float:
    """`tod`: a float in [0, 6) or a name (dawn1 .. night; 'day' = 2)."""
    if isinstance(tod, str):
        return float(TOD_NAMES.index(tod)) if tod in TOD_NAMES else float(tod)
    return float(tod) % 6.0


# ---- the setup ------------------------------------------------------------------------------------------------------

@dataclass
class GameLighting:
    biome: str
    tod: float
    vis: dict                                  # blended VisualDataComponent
    lights: dict                               # blended directional lights by name
    sh: np.ndarray                             # (9, 3) SH irradiance of the biome's cube
    player: tuple | None = (0.0, 0.0, 0.0)     # the player's feet (world), None: no player light
    extra: list = field(default_factory=list)  # more scene lights, game form {pos, color, radius, exp, diffuse}
    player_on_plate: bool | None = None        # the player's light on the shot's ground: None = only with the shadow
                                               # terms (the caster's units-slice shadow G darkens it as in the game)
    shadows: bool = True                       # shadow maps on in light() (gpu.Light.shadow {"on": True})
    inside: bool | None = None                 # an inside level (None: the biome names a cave): the player light has
                                               # the cube shadow (record word 11 = 0, -1 outdoors) and the ground's
                                               # ambient x the light radius (light_radius; terrain cb6[1] = 1)
    bloom: bool = True                         # the game's bloom in rig() (vis bloomCutoff / bloomIntensity)
    fs: object = None                          # the vfs the setup was read from (rig(): the grading LUT)
    global_slice: int = 1                      # the slice that makes G (shadow.GAME["global"])
    sources: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)

    # -- numbers --
    @property
    def exposure(self) -> float:
        return exposure_for_ev(self.vis.get("exposureValue", 4.0))

    @property
    def ibl(self) -> float:
        return float(self.vis.get("iblIntensity", 1.0))

    def dirs(self, mask: int) -> list:
        """Directional lights reaching lightMask `mask`: [{name, dir (towards), color (x power), diffuse, specular}]."""
        out = []
        for name, L in self.lights.items():
            if not (int(L["mask"]) & int(mask)) or L["power"] <= 0:
                continue
            out.append({"name": name, "dir": np.round(towards(L["quat"]), 6).tolist(),
                        "color": [round(c * L["power"], 6) for c in L["color"]], "diffuse": L["diffuse"],
                        "specular": L["specular"], "shadow": L["shadow"], "shadow_index": shadow_index(L)})
        return out

    def player_light(self) -> dict | None:
        if self.player is None:
            return None
        v = self.vis
        c = float(v.get("playerUnitLightPower", 2000.0)) / (4 * math.pi)
        con = v.get("playerUnitLightContributions") or {"x": 1.0, "y": 0.666}
        p = np.asarray(self.player, float) + (0.0, PLAYER_LIGHT_HEIGHT, 0.0)
        return {"name": "player_light", "pos": p.tolist(), "color": [c, c, c],
                "radius": PLAYER_LIGHT_RADIUS * float(v.get("playerUnitLightDistanceScaler", 1.333)),
                "exp": float(v.get("playerUnitLightAttenuation", 5.5)), "diffuse": float(con.get("x", 1.0)),
                "specular": float(con.get("y", 0.666)), "shadowed": True,       # a shadowed point (not on models)
                **({"cube": 0} if self.is_inside else {})}

    @property
    def is_inside(self) -> bool:
        return self.inside if self.inside is not None else any(k in self.biome for k in INSIDE_BIOMES)

    def unit_light(self, light: float, rgb, origin=(0.0, 0.0, 0.0)) -> dict:
        """A unit's own light (monstats2 Light, light-r/g/b; monster_light adds the unique bonus) in the game's form."""
        v = self.vis
        r = 2.0 * float(light) * float(v.get("unitLightDistanceScaler", 1.5))
        h = r * float(v.get("unitLightDistanceOffsetRatio", 0.5))
        k = float(v.get("unitLightPower", 500.0)) / (4 * math.pi) / 255.0
        return {"name": "unit_light", "pos": (np.asarray(origin, float) + (0.0, h, 0.0)).tolist(),
                "color": [c * k for c in rgb], "radius": r, "exp": float(v.get("unitLightAttenuation", 5.0)),
                "diffuse": 1.0, "specular": 1.0, "shadowed": True}               # a shadowed point (not on models)

    def scene_points(self, plate: bool = False, shadowed: bool = False) -> list:
        """The scene's local lights (player light first, then `extra`). On a shot's ground (`plate`) the player's light
        counts when player_on_plate, or (None) when the caster's shadow is modelled (`shadowed`)."""
        pts = list(self.extra)
        p = self.player_light()
        on = self.player_on_plate if self.player_on_plate is not None else shadowed
        if p and (on or not plate):
            pts.insert(0, p)
        return pts

    def value_curve(self) -> dict:
        """The unit value curve (cb7 72..79): cubic P through (0, 0), characterContrastPoints, (1, 1); strength
        characterBrightnessFactor (game_shade: V' = V + strength x P(V))."""
        cp = self.vis.get("characterContrastPoints") or {"x": 0.25, "y": 0.25, "z": 0.75, "w": 0.99}
        xs = np.array([0.0, cp["x"], cp["z"], 1.0])
        ys = np.array([0.0, cp["y"], cp["w"], 1.0])
        poly = np.linalg.solve(np.vander(xs, 4, increasing=True), ys)
        return {"poly": np.round(poly, 6).tolist(), "brightness": 0.0,
                "strength": float(self.vis.get("characterBrightnessFactor", 0.4)), "on": True}

    # -- irradiance on a surface --
    def irradiance(self, P, n=(0.0, 1.0, 0.0), mask: int = TERRAIN_MASK, visibility=None, plate: bool = True, fx=()):
        """(..., 3) what a white diffuse surface at world points P (..., 3) with normal n gets, in the game's light units
        before the albedo / pi: iblIntensity x SH(n) + G x (directional lights x N.L x their own shadow + the scene's
        local lights (player light, `extra`) in the game's falloff). The shadow terms (`visibility`, shadow_terms: G,
        slice 0, slice 1 per point) compose as ps_models_p / ps_terrain do (view/shadow.py): a light with shadow index
        i != the global slice gets G x s_i, the global slice's light G, none 1; then the whole direct sum x G again;
        ambient unshadowed. Without `visibility` every term is 1."""
        from ..view import ibl as I
        from . import packers as pk
        P = np.asarray(P, float)
        n = np.broadcast_to(np.asarray(n, float), P.shape)
        G, sl = shadow_terms(visibility, P.shape[:-1])
        cube = cube_term(visibility, P.shape[:-1])
        direct = np.zeros(P.shape[:-1] + (3,))
        for d in self.dirs(mask):
            nl = np.clip((n * np.asarray(d["dir"])).sum(-1), 0.0, None) * d["diffuse"]
            i = d["shadow_index"]
            if 0 <= i < len(sl):
                nl = nl * (G if i == self.global_slice else G * sl[i])
            direct += nl[..., None] * np.asarray(d["color"])
        for p in self.scene_points(plate=plate, shadowed=visibility is not None):
            dv = np.asarray(p["pos"], float) - P
            dist = np.sqrt((dv * dv).sum(-1))
            nl = np.clip((n * dv).sum(-1) / np.maximum(dist, 1e-4), 0.0, 1.0)
            f = pk.light_falloff(dist, float(p["radius"]), -abs(float(p["exp"]))) * nl * float(p.get("diffuse", 1.0))
            direct += (f * (cube if p.get("cube") == 0 else 1.0))[..., None] * np.asarray(p["color"], float)
        amb = self.ibl * np.maximum(I.irradiance(self.sh, n), 0.0)
        if self.is_inside:
            amb = amb * light_radius(self.scene_points() + list(fx), P, self.exposure)[..., None]
        return amb + G[..., None] * direct

    def plate_gain(self, P, fx_points, visibility=None, visibility_fx=None, n=(0.0, 1.0, 0.0)):
        """(H, W, 3) E1 / E0: the shot's surface (P (H, W, 3), normal n) relit by an effect's lights (gpu point dicts
        as packers.FxLight.point gives them, colour / pi). E0 = the surface light when the shot was taken (shadow terms
        `visibility`: the caster's own shadow, already in the shot), E1 = the same under `visibility_fx` (the caster and
        the effect's shadow casters; default `visibility`) + G x E_fx (the effect's lights are local lights: the direct
        sum's G darkens them too). Without shadow terms: 1 + E_fx / E0."""
        from . import render as R
        E0 = np.maximum(self.irradiance(P, n=n, visibility=visibility, plate=True) / math.pi, 1e-6)   # FxLight colours / pi
        vf = visibility if visibility_fx is None else visibility_fx
        fxg = [dict(q, color=[c * math.pi for c in q["color"]], exp=-float(q.get("atten", -1.0)))
               for q in fx_points if not q.get("directional")]          # the effect's lights in the light radius too
        E1 = (self.irradiance(P, n=n, visibility=vf, plate=True, fx=fxg) / math.pi
              if (vf is not visibility or (self.is_inside and fxg)) else E0)
        Efx = R.plate_gain(P, fx_points, 1.0, n=n) - 1.0
        G, _s = shadow_terms(vf, P.shape[:-1])
        return (E1 + G[..., None] * Efx) / E0

    # -- viewer light --
    def game_light(self, mask: int = MONSTER_MASK, origin=(0.0, 0.0, 0.0), unit=None) -> dict:
        """gpu.Light.game for a model with lightMask `mask` standing at `origin` (positions relative to it), plus its own
        light `unit` = (Light, (r, g, b)) if it has one."""
        o = np.asarray(origin, float)
        pts = []
        if unit:
            pts.append(self.unit_light(unit[0], unit[1], o))
        pts += self.scene_points()
        rel = [dict(p, pos=(np.asarray(p["pos"], float) - o).round(5).tolist()) for p in pts]
        dirs = []
        for d in self.dirs(mask):
            d.pop("shadow")
            i = d.pop("shadow_index")
            dirs.append(dict(d, shadow_mode=i if i >= 0 else 4294967295))      # the capture's word: -1 as uint32
        return {"dirs": dirs,
                "sh": np.asarray(self.sh, float).tolist(), "sh_terms": list(SH_TERMS),
                "ibl": {"intensity": self.ibl, "diffuse": 1.0, "specular": 1.0}, "points": rel,
                "exposure": self.exposure, "value": self.value_curve(), "direct_scale": 1.0}

    def light(self, mask: int = MONSTER_MASK, origin=(0.0, 0.0, 0.0), unit=None):
        """gpu.Light: `game` for units (game_shade); the legacy fields for effects' lit materials (fx.wgsl light_scene)
        in FxLight units (/ pi): key = the strongest terrain light, ambient = iblIntensity x SH towards the camera; the
        local lights (player, the unit's own, `extra`) as `points`, positions relative to `origin`."""
        from ..view import gpu as G, ibl as I
        dirs = self.dirs(TERRAIN_MASK)
        k = max(dirs, key=lambda d: sum(d["color"]), default=None)
        cam = np.array([0.6124, 0.5, 0.6124])                    # towards the game camera (el 30, az 45)
        amb = (self.ibl * np.maximum(I.irradiance(self.sh, cam), 0.0) / math.pi).round(6)
        base = G.Light()
        g = self.game_light(mask, origin, unit)
        # the local lights go to Light.points in FxLight form (colour / pi, "fx"): effects' lit materials see them, and
        # gpu hands them to game_shade x pi (the game's own colours), so units get them exactly once
        pts = tuple({"name": p["name"], "pos": tuple(p["pos"]), "radius": float(p["radius"]),
                     "color": [c / math.pi for c in p["color"]], "atten": -abs(float(p["exp"])), "fx": True,
                     "shadowed": bool(p.get("shadowed")), **({"cube": p["cube"]} if "cube" in p else {})}
                    for p in g.pop("points"))
        g["points"] = []
        return replace(base, ambient=tuple(amb), env_col=tuple(amb),
                       key_dir=tuple(k["dir"]) if k else base.key_dir,
                       key_col=tuple(c / math.pi for c in k["color"]) if k else (0.0, 0.0, 0.0),
                       floor_clip=0.0, points=pts, game=g, shadow={"on": True} if self.shadows else {})

    def rig(self, mask: int = MONSTER_MASK, clut=None, unit=None, origin=(0.0, 0.0, 0.0)):
        """view.rig.Rig "scene": light(), exposure from the vis EV, tone `clut` (the game's post LUT as .npy, built
        from gradingLutFile; captured once) else `lut`. `rig.lighting` is this object (fx CLI: the plate relight)."""
        from ..view import rig as Rg
        game_clut = clut in (None, "game") and self.fs is not None and self.vis.get("gradingLutFile")
        r = Rg.Rig("scene:%s;tod=%g" % (self.biome, self.tod), self.light(mask, origin, unit), self.exposure,
                   "clut" if (clut and clut != "lut") or game_clut else "lut", None, [], list(self.assumptions), [],
                   self.as_dict(), {})
        if game_clut:             # the game's Universal CLUT built from the vis file's grading LUT (view/clut.py)
            from ..view import clut as CL
            r.post["clut"] = CL.register(self.fs, self.grading_lut())
        elif clut and clut != "lut":
            r.post["clut"] = str(Path(clut).resolve())
        if self.bloom:            # the game's bloom before the tone map (view/tone.py game_bloom, from the r4 capture)
            r.post["bloom"] = {"model": "game", "cutoff": float(self.vis.get("bloomCutoff", 1.0)),
                               "intensity": float(self.vis.get("bloomIntensity", 1.0))}
        else:
            r.unknown.append("post LUT: tone 'lut' stands in (5-20 % brighter than the game's on flesh)")
        if not self.shadows:
            r.unknown.append("shadow maps off (shadows=0): no stand-in, direct_scale 1")
        r.lighting = self
        return r

    def grading_lut(self) -> str:
        """The grading LUT of the time of day: the gradingLutFile of the vis file the blend is nearer to (the r4
        capture's combine passes used the current vis file's LUT alone)."""
        names = self.sources.get("vis_luts") or []
        w = float(self.sources.get("weight", 0.0))
        if len(names) == 2:
            return names[1] if w >= 0.5 else names[0]
        return str(self.vis.get("gradingLutFile") or "data/hd/env/lut/default.dds")

    def as_dict(self) -> dict:
        return {"biome": self.biome, "tod": self.tod, "exposure": self.exposure, "ibl": self.ibl,
                "dirs_terrain": self.dirs(TERRAIN_MASK), "sh": np.asarray(self.sh).round(5).tolist(),
                "player_light": self.player_light(), "player_on_plate": self.player_on_plate, "shadows": self.shadows,
                "inside": self.is_inside,
                "extra": list(self.extra), "sources": self.sources}


# ---- loading --------------------------------------------------------------------------------------------------------

def sh_table(path=TABLE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cubes"]


def sh_for(cube: str, fs=None, table=None) -> np.ndarray:
    """(9, 3) SH of an IBL cube (logical path): the table, else decoded through the GPU from `fs` (caller gates)."""
    key = cube.replace("\\", "/").lower()
    t = sh_table() if table is None else table
    if key in t and "sh" in t[key]:
        return np.asarray(t[key]["sh"], float)
    if fs is None:
        raise KeyError("IBL cube %s not in %s and no fs to decode it" % (cube, TABLE.name))
    from ..view import ibl as I
    return I.irradiance_sh(I.decode_cube(I.read_cube(fs.read(cube))))


def for_biome(fs, biome: str, tod=2.0, player=(0.0, 0.0, 0.0), extra=(), table=None, player_on_plate=None,
              shadows=True, event=None):
    """GameLighting of a biome at `tod`, with an optional fully active VisualDataEventMap event."""
    b = json.loads(fs.read("data/hd/env/biome/%s.json" % biome).decode("utf-8-sig"))
    names = [v.get("visualDataFilename") for v in b.get("visualDataFilenames") or [] if v.get("visualDataFilename")]
    override = None
    if event:
        events = json.loads(fs.read("data/hd/env/weather/vis_event_map.json").decode("utf-8-sig"))
        entry = events[event]
        override = entry.get("definition") or entry.get("definitions", {}).get(biome, {}).get("definition")
        if override:
            names = [override]
    if not names:
        raise ValueError("biome %s lists no vis files" % biome)
    t = tod_index(tod)
    i = int(math.floor(t)) % len(names)
    j = (i + 1) % len(names)
    w = t - math.floor(t) if i != j else 0.0
    va = parse_vis(fs.read(names[i]))
    vb = parse_vis(fs.read(names[j])) if i != j else va
    v = blend(va, vb, w)
    luts = [x["visual"].get("gradingLutFile") or "data/hd/env/lut/default.dds" for x in (va, vb)]
    cube = ((b.get("iblSettings") or {}).get("cubemapFilename") or "data/hd/env/ibl/default_CUBEMAP.ibls")
    sh = sh_for(cube, fs, table)
    return GameLighting(biome, t, v["visual"], v["lights"], sh, None if player is None else tuple(map(float, player)),
                        list(extra), None if player_on_plate is None else bool(player_on_plate), bool(shadows),
                        fs=fs,
                        sources={"vis": [names[i], names[j]], "weight": round(w, 4), "cube": cube, "vis_luts": luts,
                                 **({"event": event} if override else {})},
                        assumptions=["unit light radius 2 x Light x unitLightDistanceScaler (captures: skeletons Light 2; Uber "
                         "Mephisto 3 + the unique 'light' modifier 4 = 7: monster_light)",
                         "player light %.1f above the feet, radius %g x playerUnitLightDistanceScaler (captures)"
                         % (PLAYER_LIGHT_HEIGHT, PLAYER_LIGHT_RADIUS),
                         "map/object lights only as passed (extra; for_preset reads a preset's)"])


def parse_spec(spec: str) -> dict:
    """'scene:<biome>[;tod=1.54|day][;mask=19][;unit=L,r,g,b][;player=x,y,z|none][;clut=path][;plate_player=0|1]
    [;shadows=0][;event=DenOfEvil][;ibl=1.0][;inside=0|1][;light=x,y,z,r,g,b,radius,exp ...]' or
    'scene:preset=<key>;at=x,z[;radius=40][;fxlights=0]...' (the
    biome, map lights and time of day from a preset: scene_inputs.for_preset) -> keyword dict (tod: 0..6 along dawn1,
    dawn2, day, dusk1, dusk2, night)."""
    s = spec[6:] if spec.startswith("scene:") else spec
    biome, *opts = s.split(";")
    if "=" in biome:                      # scene:preset=...;...: no biome name first
        biome, opts = None, [biome] + opts
    o = {"biome": biome, "tod": 2.0, "mask": MONSTER_MASK, "preset": None, "at": (0.0, 0.0), "radius": 40.0,
         "fx_lights": True,
         "unit": None, "player": (0.0, 0.0, 0.0), "clut": None, "player_on_plate": None, "extra": [],
         "shadows": True, "bloom": True}
    for x in opts:
        k, _, v = x.partition("=")
        if k == "tod":
            o["tod"] = float(v) if v[:1].isdigit() else v
            o["tod_given"] = True
        elif k == "preset":
            o["preset"] = v
        elif k == "at":
            o["at"] = tuple(float(z) for z in v.split(","))[:2]
        elif k == "radius":
            o["radius"] = float(v)
        elif k == "fxlights":
            o["fx_lights"] = v not in ("0", "off", "no")
        elif k == "mask":
            o["mask"] = int(v)
        elif k == "unit":
            f = [float(z) for z in v.split(",")]
            o["unit"] = (f[0], tuple(f[1:4]))
        elif k == "player":
            o["player"] = None if v in ("none", "off") else tuple(float(z) for z in v.split(","))
        elif k == "clut":
            o["clut"] = v
        elif k == "plate_player":
            o["player_on_plate"] = v not in ("0", "off", "no")
        elif k == "shadows":
            o["shadows"] = v not in ("0", "off", "no")
        elif k == "bloom":
            o["bloom"] = v not in ("0", "off", "no")
        elif k == "event":
            o["event"] = v
        elif k == "inside":          # inside-level lighting on / off (GameLighting.inside)
            o["inside"] = v not in ("0", "off", "no")
        elif k == "ibl":             # the frame's iblIntensity where it differs from the vis files (a capture's cb6[0])
            o["ibl"] = float(v)
        elif k == "ao":              # the game's screen-space AO on the viewer's units (view.assao); on by default
            o["ao"] = v not in ("0", "off", "no")
        elif k == "light":
            f = [float(z) for z in v.split(",")]
            o["extra"].append({"name": "extra%d" % len(o["extra"]), "pos": f[0:3], "color": f[3:6], "radius": f[6],
                               "exp": f[7] if len(f) > 7 else 2.0, "diffuse": 1.0, "specular": 1.0})
    return o


def resolve_rig(spec: str, fs):
    """`--rig scene:...` (parse_spec) -> view.rig.Rig with `.lighting`; any other spec through view.rig.resolve."""
    from ..view import rig as Rg
    if not (spec or "").startswith("scene:"):
        return Rg.resolve(spec)
    o = parse_spec(spec)
    if o["preset"]:
        from . import scene_inputs as SI
        L = SI.for_preset(fs, o["preset"], o["at"], o["radius"], o["tod"] if o.get("tod_given") else None,
                          o["player"], o["fx_lights"], player_on_plate=o["player_on_plate"], shadows=o["shadows"],
                          event=o.get("event"))
        L.extra += o["extra"]
    else:
        L = for_biome(fs, o["biome"], o["tod"], o["player"], o["extra"], player_on_plate=o["player_on_plate"],
                      shadows=o["shadows"], event=o.get("event"))
    L.bloom = o["bloom"]
    if o.get("inside") is not None:
        L.inside = o["inside"]
    if o.get("ibl") is not None:
        L.vis = dict(L.vis, iblIntensity=o["ibl"])
    return _ao(L.rig(o["mask"], o["clut"], o["unit"]), o)


def _ao(rig, o):
    """Spec `;ao=0`: no screen-space AO (light.game["ao"] = False, view.gpu.Renderer.ssao)."""
    if o.get("ao") is False and rig.light.game:
        rig.light = replace(rig.light, game=dict(rig.light.game, ao=False))
    return rig
