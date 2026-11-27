"""Shadow maps as D2R draws them (lane hdfx-r3-shadows; docs/hdfx.md section 3, "Shadows").

Read from the calib r3 RenderDoc capture (Rogue Encampment, Mephisto; ps_models_p eid 24399, its SPIR-V disassembled with SPIRV-Cross, with root CBV 5 and the frame's action list):

- One 4096 x 4096 depth array, two slices, both from the main directional light (the moon here: the slices' depth axes are
  that light's direction to 0.01), orthographic, reversed depth (1 at the near plane; a texel blocks when the receiver's depth
  is below it):
  - slice 0, pass `Shadows / DirectionalLight`: the static world (props, walls, plants, placed objects; `models_shadows_p`,
    `alpha_threshold_shadows_p`), no units. Half extents 168 x 109 world units, near 1, far 263.
  - slice 1, pass `Shadows / NegativeDirectionalLight`: units only (Mephisto, the player, NPCs, critters;
    `models_shadows_dither_p`, `hair_shadows_dither_p`). Half extents 97 x 65, near 130.2, far 240.7. The light's eye sits
    189.4 units from the unit's feet along the light direction (both slices share it).
- Receivers (ps_models_p): world position + 0.05 x normal, through the slice's matrix; filter per slice by mode
  (cb5 127 / 135): 1 one tap, 2 four taps at +-0.5 texel, 3 eight Poisson taps at 2 texels, 4 PCSS with 8 taps, 5 PCSS
  with 16 (both slices use 4): blocker search over light size x (d - near) / d, penumbra (d - d_blocker) / d_blocker x
  light size x near / d (perspective PCSS on the slice's view distances), light size 1.5 world units (cb5[31].xy, [33].xy
  are 1.5 / the slice's width and height). The 16-point Poisson disk is the shader's own table.
- Composition: G = 1 + 0.9 (s1 - 1) (cb5[196] 0.9). Directional light k with shadow index i (light record word 11):
  i = 0 -> G x s0, i = 1 -> G, none -> 1; then the whole direct sum (directional and local lights) x G again; ambient and
  image light are not shadowed. In r3: the moon (i 0) gets s0 G^2, the player key light (i 1) G^2, the fill light G.
- Particles cast through `ps_popcorn_uber_shadows_p` (and `ps_popcorn_default_shadows_p`): the material's alpha (masks,
  blend, colour alpha, optional soft fade) and a discard below cb4[6].w = `Shadows.ShadowThreshold` (0.4 swarm, 0.5 rocks).
  Standard meshes (`Shadows`) cast as the engine's model instances. Lit particles never read a shadow map (their shaders
  have none); units, the ground and Standard meshes (the engine's model shader) receive.

The viewer: `ShadowMaps` (one per gpu.Renderer) renders the casters of a draw call into the slices before the main pass and
binds the maps as bind group 1 of the scene and effect pipelines; `RECEIVER_WGSL` replaces the no-shadow stubs of
shaders.wgsl and fx.wgsl (the browser page keeps the stubs). `gpu.Light.shadow` switches it on (`spec_for`); `ground_gain`
gives the darkening of a game shot's ground (fx/render.py over a `bg`).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

# the game's constants (calib r3, cb5 of eid 24399), world units
GAME = {
    "size": 4096,
    "eye": 189.4,              # light eye distance from the subject (unit's feet) along the light direction
    "light_size": 1.5,         # PCSS light size, world units
    "offset": 0.05,            # receiver normal offset
    "strength": 0.9,           # cb5[196]: the units slice's strength in G
    "global": 1,               # the slice that makes G
    "slices": [
        {"name": "world", "mode": 4, "near": 1.0, "far": 263.08, "half": [168.03, 108.53], "casters": ["world"]},
        {"name": "units", "mode": 4, "near": 130.22, "far": 240.71, "half": [97.37, 65.11], "casters": ["unit", "fx"]},
    ],
}
# the shader's 16-point Poisson disk (ps_models_p _165)
POISSON = np.array([
    (-0.94201624, -0.39906216), (0.94558609, -0.76890725), (-0.09418410, -0.92938870), (0.34495938, 0.29387760),
    (-0.91588581, 0.45771432), (-0.81544232, -0.87912464), (-0.38277543, 0.27676845), (0.97484398, 0.75648379),
    (0.44323325, -0.97511554), (0.53742981, -0.47373420), (-0.26496911, -0.41893023), (0.79197514, 0.19090188),
    (-0.24188840, 0.99706507), (-0.81409955, 0.91437590), (0.19984126, 0.78641367), (0.14383161, -0.14100790)], np.float32)
MAX_SLICES = 2
MAX_LOCAL = 8              # spot / shadowed point records (ShdU.lrec)
MAX_SPOT_MAPS = 4          # the game's spot shadow indices 0..3 (light record word 8: -1 < i < 4)
SH_FLOATS = 56 + 8 + 4 * 3 * MAX_LOCAL + 16 * MAX_SPOT_MAPS   # ShdU below
# local-light shadows (lane r3-shadows2; ps_models_p's spot and shadowed-point loops, Global Attenuation's spot loop,
# cb5[192..195] of the s2 / r3 captures)
LOCAL = {
    "spot_offset": 0.2,        # receiver = position + 0.2 x normal (directional slices: 0.05)
    "spot_taps": (3.0 / 1024.0, 1.0 / 1024.0),   # cb5[194], [195]: 4 taps at (u - a | u + b, v + b | v - a)
    "spot_size": 512,          # spot map size (r1 capture: the SpotLights pass viewport 512 x 512, compare Greater)
    "spot_near": 1.0,          # z / w = 1 / d, no far plane (r1 capture: the forge spot's matrix)
    "cube_taps": (6.0 / 1024.0, 2.0 / 1024.0),   # cb5[192], [193]: 4 taps on the light -> receiver direction
    "cube_size": 512,          # cube face size (GUESS: no capture has drawn a cube)
    "cube_bias": 0.1,          # compare value (distance - 0.1) / 2000 (the shader's constants)
    "cube_range": 2000.0,
}


@dataclass
class Slice:
    name: str
    dir: tuple                 # towards the light, world axes (unit)
    mode: int = 4
    near: float = 1.0
    far: float = 263.08
    half: tuple = (168.03, 108.53)
    casters: tuple = ("world",)


@dataclass
class Spec:
    """The shadow set-up of one render: slices, which slice each directional light reads, the global slice."""
    slices: list
    lights: tuple = (0,)       # shadow index of directional light k (gpu.Light.game dirs order; the key light without one)
    global_slice: int = -1     # the slice that makes G (-1: none)
    strength: float = 0.9
    light_size: float = 1.5
    offset: float = 0.05
    size: int = 4096
    eye: float = 189.4
    centre: tuple = (0.0, 0.0, 0.0)
    direct_scale: float = 1.0  # replaces Light.game's direct_scale (its one-number stand-in for these maps)
    notes: list = field(default_factory=list)
    locals: list = field(default_factory=list)   # local_records(): spot / shadowed-point records (cone, spot map, cube)

    def matrices(self) -> list:
        """Per slice the 4x4 (row-major, column vectors) world -> (x / half_x, y / half_y, z, 1): z 1 at the near plane,
        0 at the far one (the game's reversed depth)."""
        out = []
        c = np.asarray(self.centre, float)
        for s in self.slices:
            f = np.asarray(s.dir, float)
            f = f / max(np.linalg.norm(f), 1e-9)
            up = np.array([0.0, 1.0, 0.0]) if abs(f[1]) < 0.99 else np.array([0.0, 0.0, 1.0])
            rx = np.cross(up, f)
            rx /= np.linalg.norm(rx)
            ry = np.cross(f, rx)
            eye = c + f * self.eye
            M = np.zeros((4, 4))
            M[0, :3], M[0, 3] = rx / s.half[0], -rx @ eye / s.half[0]
            M[1, :3], M[1, 3] = ry / s.half[1], -ry @ eye / s.half[1]
            # distance from the eye along -f: d = (eye - p) . f; z = (far - d) / (far - near)
            span = max(s.far - s.near, 1e-6)
            M[2, :3], M[2, 3] = f / span, (s.far - f @ eye) / span
            M[3, 3] = 1.0
            out.append(M)
        return out

    def uniforms(self) -> np.ndarray:
        """ShdU (RECEIVER_WGSL) as floats."""
        u = np.zeros(SH_FLOATS, np.float32)
        u[0:4] = (len(self.slices), self.strength, self.global_slice, self.offset)
        li = (list(self.lights) + [-1] * 4)[:4]
        u[4:8] = li
        for k, (s, M) in enumerate(zip(self.slices, self.matrices())):
            u[8 + 16 * k:24 + 16 * k] = M.T.ravel()
            u[40 + 4 * k:44 + 4 * k] = (s.mode, 1.0 / self.size, self.light_size / (2.0 * s.half[0]),
                                        self.light_size / (2.0 * s.half[1]))
            u[48 + 4 * k:52 + 4 * k] = (s.near, s.far, 0.0, 0.0)
        return local_uniforms(self.locals, u)

    def as_dict(self) -> dict:
        return {"slices": [dict(vars(s)) for s in self.slices], "lights": list(self.lights), "global": self.global_slice,
                "strength": self.strength, "light_size": self.light_size, "offset": self.offset, "size": self.size,
                "eye": self.eye, "centre": [float(x) for x in self.centre], "direct_scale": self.direct_scale,
                "notes": list(self.notes),
                "locals": [{k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in r.items() if k != "M"}
                           for r in self.locals]}


def spec_for(light, centre=(0.0, 0.0, 0.0)) -> Spec | None:
    """The Spec of a gpu.Light whose `shadow` dict is on ({"on": true} takes the game's set-up; keys of GAME override it,
    `slices` entries per slice, plus `lights` and `direct_scale`). With Light.game: both slices along the light whose
    shadow index (dirs[k].shadow_mode) is 0, else the first; the lights' shadow indices from shadow_mode. Without: one slice
    along the key light holding every caster, read by the key light only. None: off."""
    sd = getattr(light, "shadow", None) or {}
    if not sd or not sd.get("on", True):
        return None
    g = getattr(light, "game", None) or {}
    dirs = list(g.get("dirs") or [])
    cfg = {k: sd.get(k, v) for k, v in GAME.items() if k != "slices"}
    notes = []
    if dirs:
        idx = [int(d.get("shadow_mode", -1)) for d in dirs]
        idx = [i if 0 <= i < MAX_SLICES else -1 for i in idx]
        main = next((d for d, i in zip(dirs, idx) if i == 0), dirs[0])
        L = np.asarray(main["dir"], float)
        base = [dict(s) for s in GAME["slices"]]
        gs, strength = int(cfg["global"]), float(cfg["strength"])
        lights = tuple(sd.get("lights", idx))
    else:
        L = np.asarray(light.key_dir, float)
        base = [{"name": "all", "mode": 4, "near": GAME["slices"][1]["near"], "far": GAME["slices"][1]["far"],
                 "half": GAME["slices"][1]["half"], "casters": ["world", "unit", "fx"]}]
        gs, strength = -1, 1.0
        lights = tuple(sd.get("lights", (0,)))
        notes.append("no game light: one slice along the key light, all casters, key light only")
    for k, over in enumerate(sd.get("slices") or []):
        if k < len(base):
            base[k].update(over)
    slices = [Slice(name=s["name"], dir=tuple(s.get("dir") or L / max(np.linalg.norm(L), 1e-9)), mode=int(s["mode"]),
                    near=float(s["near"]), far=float(s["far"]), half=tuple(float(h) for h in s["half"]),
                    casters=tuple(s["casters"])) for s in base[:MAX_SLICES]]
    return Spec(slices, lights, gs, strength, float(cfg["light_size"]), float(cfg["offset"]), int(cfg["size"]),
                float(cfg["eye"]), tuple(float(x) for x in (sd.get("centre") or centre)),
                float(sd.get("direct_scale", 1.0)), notes, local_records(light))


def spot_matrix(pos, axis, cone: float, radius: float = 0.0, near: float | None = None) -> np.ndarray:
    """World -> spot map clip (row-major, column vectors) as the game builds it (r3-shadows2, r1 capture frame 4928: the
    forge spot's matrix in registers._m11): perspective from `pos` along `axis` (the light's travel direction), half angle
    acos(cone) to the map's edge, reversed depth with no far plane: z / w = near / d (near 1: 1 at one unit, 0.04 at 25).
    x / w, y / w in -1..1 (the game's matrix gives the uv directly: the same up to the 0.5 scale and offset). `radius` is
    kept for callers; the projection has no far plane."""
    near = float(LOCAL["spot_near"] if near is None else near)
    f = np.asarray(axis, float)
    f = f / max(np.linalg.norm(f), 1e-9)
    up = np.array([0.0, 1.0, 0.0]) if abs(f[1]) < 0.99 else np.array([0.0, 0.0, 1.0])
    rx = np.cross(f, up)
    rx /= np.linalg.norm(rx)
    ry = np.cross(rx, f)
    half = math.acos(min(max(float(cone), -0.999), 0.999))
    g = 1.0 / max(math.tan(min(half, math.radians(89.0))), 1e-4)
    p = np.asarray(pos, float)
    M = np.zeros((4, 4))
    M[0, :3], M[0, 3] = g * rx, -g * rx @ p
    M[1, :3], M[1, 3] = g * ry, -g * ry @ p
    M[2, 3] = near
    M[3, :3], M[3, 3] = f, -f @ p
    return M


def spot_from_preset(comp: dict, xf, shadow: int = -1, gain: float = 1.0) -> dict:
    """A gpu point-light dict with `spot` from a preset's SpotLightDefinitionComponent and its entity's 4x4 transform
    (preset.trs): position, travel direction = the transform's +Z (the forge spot of the r1 capture: the game's spot
    matrix axis is the preset orientation's +Z to 1e-4), cone = cos(angle / 2) (the game's map edge for its 90 degree
    spot; the record's cone word itself was not on screen: GUESS that it is the same), radius = distance, colour x power
    x gain x diffuseContribution, atten = -attenuation (D2R's falloff exponent form, GUESS for spots). `shadow`: the spot
    map index (0..3) when the component is a shadowCaster, else -1."""
    M = np.asarray(xf, float)
    f = M[:3, :3] @ np.array([0.0, 0.0, 1.0])
    col = comp.get("color") or {}
    pw = float(comp.get("power", 0.0)) * gain * float(comp.get("diffuseContribution", 1.0))
    return {"name": comp.get("name", "spot"), "pos": tuple(float(v) for v in M[:3, 3]),
            "radius": float(comp.get("distance", 20.0)), "atten": -abs(float(comp.get("attenuation", 2.0))),
            "color": [pw * float(col.get(k, 1.0)) for k in "xyz"],
            "spot": {"dir": (f / max(np.linalg.norm(f), 1e-9)).tolist(),
                     "cone": math.cos(math.radians(float(comp.get("angle", 45.0))) / 2.0),
                     "shadow": int(shadow) if comp.get("shadowCaster") else -1}}


def local_records(light) -> list:
    """Spot lights and shadowed point lights of a gpu.Light (its `points` and Light.game's points), as ps_models_p reads
    them: a point dict with `spot` = {"dir": travel direction, "cone": c (cos of the half angle: the cone factor is
    clamp((dot(dir, -L) - c) / (1 - c))^2), "shadow": spot map index 0..3 or -1} is a spot; one with `cube` = 0 is a
    shadowed point with the one cube map (the game: index 0 only; -1 or absent = no shadow). At most MAX_LOCAL."""
    from . import gpu as G
    gm = getattr(light, "game", None) or {}
    pts = list(G.game_points(gm)) + [q for q in (getattr(light, "points", None) or ()) if not q.get("directional")]
    out, spots = [], 0
    for q in pts:
        sp, cube = q.get("spot"), q.get("cube", -1)
        if not isinstance(sp, dict) and (cube is None or int(cube) != 0):
            continue
        r = {"name": q.get("name", ""), "pos": np.asarray(q["pos"], float), "radius": float(q.get("radius", 10.0))}
        if isinstance(sp, dict):
            d = np.asarray(sp.get("dir", (0.0, -1.0, 0.0)), float)
            r.update(kind="spot", dir=d / max(np.linalg.norm(d), 1e-9), cone=float(sp.get("cone", 0.7071)),
                     shadow=int(sp.get("shadow", -1)))
            if not 0 <= r["shadow"] < MAX_SPOT_MAPS:
                r["shadow"] = -1
            if r["shadow"] >= 0:
                r["M"] = spot_matrix(r["pos"], r["dir"], r["cone"], r["radius"], sp.get("near"))
                spots += 1
        else:
            r.update(kind="cube", dir=np.zeros(3), cone=-2.0, shadow=0)
        out.append(r)
        if len(out) >= MAX_LOCAL:
            break
    return out


# cube faces in WebGPU's order (+X, -X, +Y, -Y, +Z, -Z): (forward, right, up) so that a direction's face uv
# (u = (sc / ma + 1) / 2, v = (tc / ma + 1) / 2, the D3D / Vulkan table) is the face render's (x_ndc + 1) / 2, (1 - y_ndc) / 2
CUBE_FACES = (((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
              ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
              ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)))


def cube_face_matrices(pos) -> list:
    """World -> face clip of the 6 cube faces round `pos` (90 degrees; clip z unused: the caster writes its depth)."""
    p = np.asarray(pos, float)
    out = []
    for f, r, u in CUBE_FACES:
        f, r, u = (np.asarray(v, float) for v in (f, r, u))
        M = np.zeros((4, 4))
        M[0, :3], M[0, 3] = r, -r @ p
        M[1, :3], M[1, 3] = u, -u @ p
        M[2, :3], M[2, 3] = 0.5 * f, -0.5 * f @ p
        M[3, :3], M[3, 3] = f, -f @ p
        out.append(M)
    return out


def local_uniforms(recs, u=None) -> np.ndarray:
    """ShdU's local block (floats 56..): count and tap offsets, per record (pos, kind), (dir, cone), (spot map index /
    cube, 0, 0, 0), the spot matrices by map index."""
    if u is None:
        u = np.zeros(SH_FLOATS, np.float32)
    sa, sb = LOCAL["spot_taps"]
    ca, cb = LOCAL["cube_taps"]
    u[56:60] = (len(recs), sa, sb, ca)
    u[60:64] = (cb, LOCAL["spot_offset"], LOCAL["cube_bias"], 1.0 / LOCAL["cube_range"])
    for k, r in enumerate(recs[:MAX_LOCAL]):
        o = 64 + 12 * k
        u[o:o + 3], u[o + 3] = r["pos"], {"spot": 1.0, "cube": 2.0}[r["kind"]]
        u[o + 4:o + 7], u[o + 7] = r["dir"], r["cone"]
        u[o + 8] = r["shadow"]
        if r["kind"] == "spot" and r["shadow"] >= 0:
            m = 64 + 12 * MAX_LOCAL + 16 * r["shadow"]
            u[m:m + 16] = np.asarray(r["M"], np.float32).T.ravel()
    return u


def caster_threshold(r) -> float | None:
    """The shadow alpha threshold of a renderer (rspec.RSpec / hd_particles renderer: `.props` or `.get`), None when it casts
    no shadow. Read from the game's material set-up (lane r3-shadows2, the dumped D2R image, build 93847; bin/getters.py in
    the lane's output folder): the Default material (0x141013d30) and the Uber material (0x14102c000) both read only
    `Shadows` (cast flag, material +0x94) and `Shadows.ShadowThreshold` (material +0x90 -> cb4[6].w, 0.0 when the
    property is absent). `Lit.CastShadows` (and `LegacyLit.CastShadows`, `CastShadows`) are registered by PopcornFX's
    RenderHelpers but no code reads them: a Default mesh with only `Lit.CastShadows` casts nothing (LIT_CASTS_SHADOWS)."""
    get = r.get if hasattr(r, "get") else (lambda k, d=None: r.props.get(k, d))

    def on(k):
        v = get(k)
        v = getattr(v, "value", v)
        if isinstance(v, (bytes, bytearray)):
            return any(v)
        if isinstance(v, (tuple, list)):
            return bool(v) and bool(v[0])
        return bool(v)

    def num(k, d):
        v = get(k)
        v = getattr(v, "value", v)
        if isinstance(v, (bytes, bytearray)) and len(v) >= 4:
            return float(np.frombuffer(bytes(v[:4]), "<f4")[0])
        if isinstance(v, (tuple, list)):
            v = v[0] if v else None
        return float(v) if isinstance(v, (int, float)) else d
    if on("Shadows"):
        return num("Shadows.ShadowThreshold", 0.0)
    if LIT_CASTS_SHADOWS and on("Lit.CastShadows"):
        return 0.0
    return None


# the game never reads `Lit.CastShadows` (see caster_threshold); True restores the old guess (cast at threshold 0)
LIT_CASTS_SHADOWS = False


# ---- WGSL --------------------------------------------------------------------------------------------------------------
_POI = ", ".join("vec2<f32>(%.8f, %.8f)" % (float(a), float(b)) for a, b in POISSON)

# Replaces the block between "// @shadow-stub-begin" and "// @shadow-stub-end" of shaders.wgsl / fx.wgsl (bind group 1).
RECEIVER_WGSL = """// shadow maps (hdkit/view/shadow.py): D2R's two directional slices, filtered per slice mode
struct ShdU {
  on: vec4<f32>,                 // x number of slices (0: off), y strength of the global slice, z global slice (-1 none), w normal offset
  lidx: vec4<f32>,               // shadow index of directional light 0..3 (-1: none)
  m: array<mat4x4<f32>, 2>,      // world -> (x / half x, y / half y, z: 1 near, 0 far)
  p: array<vec4<f32>, 2>,        // x mode (1..5), y texel (1 / size), zw light size in uv (x, y)
  q: array<vec4<f32>, 2>,        // x near, y far (view distances of the slice)
  loc: vec4<f32>,                // x local records, yz spot taps (a, b), w cube tap a
  loc2: vec4<f32>,               // x cube tap b, y spot receiver normal offset, z cube bias, w 1 / cube range
  lrec: array<vec4<f32>, 24>,    // per record: (pos, kind 1 spot / 2 cube), (travel dir, cone c), (spot map index, ...)
  sm: array<mat4x4<f32>, 4>,     // spot map matrices by index (perspective, reversed depth)
};
@group(1) @binding(0) var<uniform> shd: ShdU;
@group(1) @binding(1) var shd_map: texture_depth_2d_array;
@group(1) @binding(2) var shd_cmp: sampler_comparison;
@group(1) @binding(3) var shd_spot: texture_depth_2d_array;
@group(1) @binding(4) var shd_cube: texture_depth_cube;
@group(1) @binding(5) var shd_ccmp: sampler_comparison;
var<private> SHD_POI: array<vec2<f32>, 16> = array<vec2<f32>, 16>(%s);

fn shd_out(uv: vec2<f32>) -> bool { return any(uv < vec2<f32>(0.0)) || any(uv > vec2<f32>(1.0)); }
fn shd_tap(uv: vec2<f32>, k: i32, z: f32) -> f32 {
  if (shd_out(uv)) { return 1.0; }
  return textureSampleCompareLevel(shd_map, shd_cmp, uv, k, z);
}
fn shd_depth(uv: vec2<f32>, k: i32) -> f32 {
  if (shd_out(uv)) { return 0.0; }
  let dim = vec2<i32>(textureDimensions(shd_map));
  let ij = clamp(vec2<i32>(floor(uv * vec2<f32>(dim))), vec2<i32>(0), dim - vec2<i32>(1));
  return textureLoad(shd_map, ij, k, 0);
}
// visibility (1 lit) of slice k at world point wpos with normal n (ps_models_p, modes 1..5)
fn shd_slice(k: i32, wpos: vec3<f32>, n: vec3<f32>) -> f32 {
  let p = shd.m[k] * vec4<f32>(wpos + n * shd.on.w, 1.0);
  if (p.z > 1.0) { return 1.0; }                       // nearer the light than the slice's near plane
  let uv = vec2<f32>(0.5 + 0.5 * p.x, 0.5 - 0.5 * p.y);
  let z = clamp(p.z, 1e-5, 1.0);
  let mode = i32(shd.p[k].x + 0.5);
  let tx = shd.p[k].y;
  if (mode <= 0) { return 1.0; }
  if (mode == 1) { return shd_tap(uv, k, z); }
  if (mode == 2) {
    let h = vec2<f32>(0.5 * tx);
    return (shd_tap(uv + h, k, z) + shd_tap(uv + vec2<f32>(-h.x, h.y), k, z) + shd_tap(uv + vec2<f32>(h.x, -h.y), k, z)
            + shd_tap(uv - h, k, z)) * 0.25;
  }
  if (mode == 3) {
    var s = 0.0;
    for (var i = 0; i < 8; i = i + 1) { s = s + shd_tap(uv + SHD_POI[i] * 2.0 * tx, k, z); }
    return s * 0.125;
  }
  let nt = select(8, 16, mode >= 5);
  let near = shd.q[k].x;
  let far = shd.q[k].y;
  let dr = far - z * (far - near);                    // receiver distance from the light's eye
  let ls = shd.p[k].zw;
  let search = ls * max(dr - near, 0.0) / dr;
  var cnt = 0.0;
  var sum = 0.0;
  for (var i = 0; i < nt; i = i + 1) {
    let d = shd_depth(uv + SHD_POI[i] * search, k);
    if (z < d) { cnt = cnt + 1.0; sum = sum + d; }
  }
  if (cnt < 1.0) { return 1.0; }
  let db = far - (sum / cnt) * (far - near);
  let w = abs((dr - db) / db);
  let rad = ls * w * near / dr;
  var s = 0.0;
  for (var i = 0; i < nt; i = i + 1) { s = s + shd_tap(uv + SHD_POI[i] * rad, k, z); }
  return s / f32(nt);
}
// G: the global slice's visibility at its strength (1 when off)
fn shd_global(wpos: vec3<f32>, n: vec3<f32>) -> f32 {
  if (shd.on.x < 0.5 || shd.on.z < -0.5) { return 1.0; }
  return 1.0 + shd.on.y * (shd_slice(i32(shd.on.z + 0.5), wpos, n) - 1.0);
}
// directional light j's own factor (before the direct sum's G): its slice x G, G for the global slice, 1 without one
fn shd_light(j: i32, wpos: vec3<f32>, n: vec3<f32>, g: f32) -> f32 {
  if (shd.on.x < 0.5 || j > 3) { return 1.0; }
  let i = i32(round(shd.lidx[j]));
  if (i < 0 || i >= i32(shd.on.x + 0.5)) { return 1.0; }
  if (f32(i) == shd.on.z) { return g; }
  return g * shd_slice(i, wpos, n);
}
// spot map k at world point p (ps_models_p / Global Attenuation: 4 compare taps at (u - a | u + b, v + b | v - a))
fn shd_spot_vis(k: i32, p: vec3<f32>) -> f32 {
  let c = shd.sm[k] * vec4<f32>(p, 1.0);
  if (c.w <= 1e-4) { return 1.0; }
  let q = c.xyz / c.w;
  if (q.z > 1.0 || q.z < 0.0) { return 1.0; }
  let uv = vec2<f32>(0.5 + 0.5 * q.x, 0.5 - 0.5 * q.y);
  if (shd_out(uv)) { return 1.0; }
  let a = shd.loc.y;
  let b = shd.loc.z;
  return (textureSampleCompareLevel(shd_spot, shd_cmp, uv + vec2<f32>(-a, b), k, q.z)
        + textureSampleCompareLevel(shd_spot, shd_cmp, uv + vec2<f32>(b, b), k, q.z)
        + textureSampleCompareLevel(shd_spot, shd_cmp, uv + vec2<f32>(-a, -a), k, q.z)
        + textureSampleCompareLevel(shd_spot, shd_cmp, uv + vec2<f32>(b, -a), k, q.z)) * 0.25;
}
// the shadowed point's cube (index 0): linear (distance - 0.1) / 2000 against the light -> receiver direction, 4 taps
fn shd_cube_vis(lpos: vec3<f32>, p: vec3<f32>) -> f32 {
  let dv = p - lpos;
  let d = max(length(dv), 1e-4);
  let r = dv / d;
  let z = (d - shd.loc2.z) * shd.loc2.w;
  let a = shd.loc.w;
  let b = shd.loc2.x;
  return (textureSampleCompareLevel(shd_cube, shd_ccmp, r + vec3<f32>(b, -a, b), z)
        + textureSampleCompareLevel(shd_cube, shd_ccmp, r + vec3<f32>(-a, b, -a), z)
        + textureSampleCompareLevel(shd_cube, shd_ccmp, r + vec3<f32>(b, b, b), z)
        + textureSampleCompareLevel(shd_cube, shd_ccmp, r + vec3<f32>(-a, -a, -a), z)) * 0.25;
}
// the factor of the local light at lpos on wpos: a spot's cone (and its map when `shadowed` and the maps are on), a
// shadowed point's cube; 1 for a plain point light (records are matched by position: draws re-pick their lights)
fn shd_local(lpos: vec3<f32>, wpos: vec3<f32>, n: vec3<f32>, shadowed: f32) -> f32 {
  let nl = i32(shd.loc.x + 0.5);
  for (var k = 0; k < nl; k = k + 1) {
    let r0 = shd.lrec[3 * k];
    if (distance(r0.xyz, lpos) > 1e-3) { continue; }
    let r1 = shd.lrec[3 * k + 1];
    let idx = i32(round(shd.lrec[3 * k + 2].x));
    let maps = shadowed > 0.5 && shd.on.x > 0.5;
    if (r0.w < 1.5) {
      let l = normalize(lpos - wpos);
      let t = clamp((dot(r1.xyz, -l) - r1.w) / max(1.0 - r1.w, 1e-4), 0.0, 1.0);
      var f = t * t;
      if (maps && idx >= 0) { f = f * shd_spot_vis(idx, wpos + n * shd.loc2.y); }
      return f;
    }
    if (maps) { return shd_cube_vis(lpos, wpos); }
    return 1.0;
  }
  return 1.0;
}
""" % _POI

STUB_RE = re.compile(r"// @shadow-stub-begin.*?// @shadow-stub-end", re.S)


def patch(src: str) -> str:
    """A shader source with its no-shadow stub block replaced by RECEIVER_WGSL (bind group 1)."""
    if not STUB_RE.search(src):
        raise ValueError("no // @shadow-stub-begin ... // @shadow-stub-end block in the shader")
    return STUB_RE.sub(lambda _m: RECEIVER_WGSL, src, count=1)


# unit model casters (gpu.Renderer draws: shaders.wgsl vertex format, the draw's own bind group 0 for the alpha cut)
CASTER_WGSL = """
struct U0 { mvp: mat4x4<f32>, view_dir: vec4<f32>, ambient: vec4<f32>, key_dir: vec4<f32>, key_col: vec4<f32>,
            env_col: vec4<f32>, p0: vec4<f32>, p1: vec4<f32> };
@group(0) @binding(0) var<uniform> u: U0;
@group(0) @binding(1) var samp: sampler;
@group(0) @binding(2) var t_alb: texture_2d<f32>;
struct CU { m: mat4x4<f32> };
@group(1) @binding(0) var<uniform> cu: CU;
struct VO { @builtin(position) clip: vec4<f32>, @location(0) uv: vec2<f32>, @location(1) wy: f32, @location(2) zw: vec2<f32>,
            @location(3) wpos: vec3<f32> };
@vertex
fn vs_cast(@location(0) pos: vec3<f32>, @location(3) uv: vec2<f32>) -> VO {
  var o: VO;
  o.clip = cu.m * vec4<f32>(pos, 1.0);
  // depth clamp (the game's casters: depthClampEnable): the slice z goes to the fragment stage and the clip z stays inside
  // the volume, so casters before the near plane (or past the far one) are kept at the clamped depth instead of clipped
  o.zw = o.clip.zw;                 // z / w per fragment (both world-linear: exact for the spots' perspective too)
  o.clip.z = 0.5 * o.clip.w;
  o.uv = uv;
  o.wy = pos.y;
  o.wpos = pos;
  return o;
}
@fragment
fn fs_cast(i: VO) -> @builtin(frag_depth) f32 {
  let a = textureSample(t_alb, samp, i.uv).a;
  if (u.p1.z > 0.5 && i.wy < 0.0) { discard; }
  if (a < u.p0.w) { discard; }
  return clamp(i.zw.x / i.zw.y, 0.0, 1.0);
}
// the shadowed point's cube faces: linear (distance - bias) / range (ps_models_p's compare value), no clamp needed
struct CL { lp: vec4<f32>, lp2: vec4<f32> };    // light position, bias; x 1 / range
@group(2) @binding(0) var<uniform> cl: CL;
@fragment
fn fs_cast_cube(i: VO) -> @builtin(frag_depth) f32 {
  let a = textureSample(t_alb, samp, i.uv).a;
  if (u.p1.z > 0.5 && i.wy < 0.0) { discard; }
  if (a < u.p0.w) { discard; }
  return clamp((length(i.wpos - cl.lp.xyz) - cl.lp.w) * cl.lp2.x, 0.0, 1.0);
}
"""

# effect casters: appended to fx.wgsl; vs_fx with the slice's matrix in u.mvp, threshold in u.pad0.y
FX_CASTER_WGSL = """
// ps_popcorn_uber_shadows_p / default_shadows_p: the material's alpha, discarded below the threshold (cb4[6].w)
// depth clamp as the game's caster pipelines (depthClampEnable): clip z kept inside the volume, the slice z (u.mvp is the
// slice matrix, orthographic: w 1) recomputed from the world position and clamped
@vertex
fn vs_shadow_fx(v: VIn) -> VOut {
  var o = vs_fx(v);
  o.clip.z = 0.5 * o.clip.w;
  return o;
}
@fragment
fn fs_shadow_fx(i: VOut) -> @builtin(frag_depth) f32 {
  var a = 1.0;
  if (u.lit.x > 2.5) {
    a = textureSample(t0, s_rep, uvso(i.uv.xy, i.so_base)).a * i.color.a;
  } else {
    a = uber(i).a;
  }
  if (a < u.pad0.y) { discard; }
  let c = u.mvp * vec4<f32>(i.wpos, 1.0);
  return clamp(c.z / c.w, 0.0, 1.0);
}
"""

# the ground of a game shot (fx/render.py over a bg): per pixel, the plane y = h's direct + ambient irradiance with these
# shadows over the same without them (rgb)
GROUND_WGSL = """
struct GU {
  r: vec4<f32>, up: vec4<f32>, w: vec4<f32>,  // camera basis; r.w = scale k (pixels per unit), up.w = plane height, w.w = plate width
  o: vec4<f32>,                                // xy origin pixel, z number of directional lights, w number of points
  amb: vec4<f32>,                              // ambient irradiance on the plane (rgb)
  dd: array<vec4<f32>, 4>,                     // directional: xyz towards the light, w its shadow index (-1 none)
  dc: array<vec4<f32>, 4>,                     // rgb irradiance at N.L = 1
  pp: array<vec4<f32>, 8>,                     // points: xyz, w radius
  pc: array<vec4<f32>, 8>,                     // rgb colour, w atten (point_falloff)
};
@group(0) @binding(0) var<uniform> gu: GU;
fn g_falloff(d: f32, r: f32, w: f32) -> f32 {
  if (w < 0.0) {
    let g = max(1.0 - pow(min(d / max(r, 1e-6), 1.0), -w), 0.0);
    return g * g / (1.0 + 0.092903040349 * d * d);
  }
  let q = clamp(1.0 - (d * d) / max(r * r, 1e-6), 0.0, 1.0);
  return pow(q, w);
}
// shd_light by the light's own shadow index (the terrain's light list is not the unit's: its indices differ)
fn shd_dir(si: f32, p: vec3<f32>, n: vec3<f32>, g: f32) -> f32 {
  let i = i32(round(si));
  if (shd.on.x < 0.5 || i < 0 || i >= i32(shd.on.x + 0.5)) { return 1.0; }
  if (f32(i) == shd.on.z) { return g; }
  return g * shd_slice(i, p, n);
}
@vertex
fn vs_full(@builtin(vertex_index) vi: u32) -> @builtin(position) vec4<f32> {
  let x = f32((vi << 1u) & 2u) * 2.0 - 1.0;
  let y = f32(vi & 2u) * 2.0 - 1.0;
  return vec4<f32>(x, y, 0.0, 1.0);
}
@fragment
fn fs_ground(@builtin(position) fc: vec4<f32>) -> @location(0) vec4<f32> {
  let a = (fc.x - gu.o.x) / gu.r.w;
  let b = -(fc.y - gu.o.y) / gu.r.w;
  var p = a * gu.r.xyz + b * gu.up.xyz;
  let c = (gu.up.w - p.y) / select(gu.w.y, 1e-9, abs(gu.w.y) < 1e-9);
  p = p + c * gu.w.xyz;
  let n = vec3<f32>(0.0, 1.0, 0.0);
  let g = shd_global(p, n);
  var lit = gu.amb.rgb;
  var bare = gu.amb.rgb;
  var direct = vec3<f32>(0.0);
  for (var k = 0; k < i32(gu.o.z + 0.5); k = k + 1) {
    let e = gu.dc[k].rgb * max(gu.dd[k].y, 0.0);
    direct = direct + e * shd_dir(gu.dd[k].w, p, n, g);
    bare = bare + e;
  }
  for (var k = 0; k < i32(gu.o.w + 0.5); k = k + 1) {
    let dv = gu.pp[k].xyz - p;
    let d = max(length(dv), 1e-4);
    let e = gu.pc[k].rgb * g_falloff(d, gu.pp[k].w, gu.pc[k].w) * max(dv.y / d, 0.0);
    direct = direct + e * shd_local(gu.pp[k].xyz, p, n, 1.0);     // the spot's cone and map / the cube
    bare = bare + e * shd_local(gu.pp[k].xyz, p, n, 0.0);         // the cone only
  }
  lit = lit + direct * g;
  return vec4<f32>(lit / max(bare, vec3<f32>(1e-6)), 1.0);
}
// Plate shadow terms: G, slice 0, slice 1 and the point cube (sampled at the bare position).
fn plate_vis(p: vec3<f32>, n: vec3<f32>) -> vec4<f32> {
  var s0 = 1.0;
  var s1 = 1.0;
  if (shd.on.x > 0.5) { s0 = shd_slice(0, p, n); }
  if (shd.on.x > 1.5) { s1 = shd_slice(1, p, n); }
  var cv = 1.0;
  for (var k = 0; k < i32(shd.loc.x + 0.5); k = k + 1) {
    if (shd.on.x > 0.5 && shd.lrec[3 * k].w > 1.5) { cv = shd_cube_vis(shd.lrec[3 * k].xyz, p); break; }
  }
  return vec4<f32>(shd_global(p, n), s0, s1, cv);
}
fn plate_ray(fc: vec4<f32>) -> vec3<f32> {
  let a = (fc.x - gu.o.x) / gu.r.w;
  let b = -(fc.y - gu.o.y) / gu.r.w;
  return a * gu.r.xyz + b * gu.up.xyz;
}
@fragment
fn fs_vis(@builtin(position) fc: vec4<f32>) -> @location(0) vec4<f32> {
  let p = plate_ray(fc);
  let c = (gu.up.w - p.y) / select(gu.w.y, 1e-9, abs(gu.w.y) < 1e-9);
  return plate_vis(p + c * gu.w.xyz, vec3<f32>(0.0, 1.0, 0.0));
}
@group(0) @binding(1) var<storage, read> plate_receivers: array<vec4<f32>>;
@fragment
fn fs_surface_vis(@builtin(position) fc: vec4<f32>) -> @location(0) vec4<f32> {
  let receiver = plate_receivers[u32(fc.y) * u32(gu.w.w) + u32(fc.x)];
  return plate_vis(plate_ray(fc) + receiver.w * gu.w.xyz, receiver.xyz);
}
"""


# ---- GPU -----------------------------------------------------------------------------------------------------------------
class ShadowMaps:
    """The shadow maps of one gpu.Renderer: bind group 1 of its scene pipelines and of fx/render.py's (`bgl`, `bind`)."""

    def __init__(self, gpu):
        self.gpu = gpu
        wgpu = gpu.wgpu
        dev = gpu.device
        T = wgpu.ShaderStage
        self.bgl = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.FRAGMENT | T.VERTEX, "buffer": {"type": "uniform"}},
            {"binding": 1, "visibility": T.FRAGMENT, "texture": {"sample_type": "depth", "view_dimension": "2d-array"}},
            {"binding": 2, "visibility": T.FRAGMENT, "sampler": {"type": "comparison"}},
            {"binding": 3, "visibility": T.FRAGMENT, "texture": {"sample_type": "depth", "view_dimension": "2d-array"}},
            {"binding": 4, "visibility": T.FRAGMENT, "texture": {"sample_type": "depth", "view_dimension": "cube"}},
            {"binding": 5, "visibility": T.FRAGMENT, "sampler": {"type": "comparison"}}])
        self.cmp = dev.create_sampler(address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge", mag_filter="linear",
                                      min_filter="linear", compare="greater-equal")
        # the cube holds linear distance (near 0): lit when the receiver's value is at or below the stored one
        self.ccmp = dev.create_sampler(address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge",
                                       address_mode_w="clamp-to-edge", mag_filter="linear", min_filter="linear",
                                       compare="less-equal")
        self.ubuf = dev.create_buffer(size=SH_FLOATS * 4, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.tex = None
        self.size = 0
        self.spot_tex = self.cube_tex = None
        self.spot_size = self.cube_size = 0
        self.spec = None
        self._alloc(1, 1, 1)
        self.bgl_c = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.VERTEX, "buffer": {"type": "uniform"}}])
        n_c = MAX_SLICES + MAX_SPOT_MAPS + 6                 # slices, spot maps, cube faces
        self.cbufs = [dev.create_buffer(size=64, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
                      for _ in range(n_c)]
        self.cbinds = [dev.create_bind_group(layout=self.bgl_c, entries=[
            {"binding": 0, "resource": {"buffer": b, "offset": 0, "size": 64}}]) for b in self.cbufs]
        self.bgl_l = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.FRAGMENT, "buffer": {"type": "uniform"}}])
        self.lbuf = dev.create_buffer(size=32, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.lbind = dev.create_bind_group(layout=self.bgl_l, entries=[
            {"binding": 0, "resource": {"buffer": self.lbuf, "offset": 0, "size": 32}}])
        self._p_model = None
        self._p_cube = None
        self._fx = {}
        self._ground = None
        self.stats = {}

    def _alloc(self, size, spot=None, cube=None):
        spot = spot or self.spot_size
        cube = cube or self.cube_size
        if self.tex is not None and (self.size, self.spot_size, self.cube_size) == (size, spot, cube):
            return
        U = self.gpu.wgpu.TextureUsage
        dev = self.gpu.device
        if self.tex is None or self.size != size:
            self.tex = dev.create_texture(size=(size, size, MAX_SLICES), format="depth32float",
                                          usage=U.RENDER_ATTACHMENT | U.TEXTURE_BINDING)
        if self.spot_tex is None or self.spot_size != spot:
            self.spot_tex = dev.create_texture(size=(spot, spot, MAX_SPOT_MAPS), format="depth32float",
                                               usage=U.RENDER_ATTACHMENT | U.TEXTURE_BINDING)
        if self.cube_tex is None or self.cube_size != cube:
            self.cube_tex = dev.create_texture(size=(cube, cube, 6), format="depth32float",
                                               usage=U.RENDER_ATTACHMENT | U.TEXTURE_BINDING)
        self.size, self.spot_size, self.cube_size = size, spot, cube
        self.bind = dev.create_bind_group(layout=self.bgl, entries=[
            {"binding": 0, "resource": {"buffer": self.ubuf, "offset": 0, "size": SH_FLOATS * 4}},
            {"binding": 1, "resource": self.tex.create_view(dimension="2d-array", aspect="depth-only")},
            {"binding": 2, "resource": self.cmp},
            {"binding": 3, "resource": self.spot_tex.create_view(dimension="2d-array", aspect="depth-only")},
            {"binding": 4, "resource": self.cube_tex.create_view(dimension="cube", aspect="depth-only")},
            {"binding": 5, "resource": self.ccmp}])

    def off(self, light=None):
        """Receivers read 'no shadow' (the uniform's slice count 0); a light's spot cones still apply (local_records)."""
        self.spec = None
        u = local_uniforms(local_records(light)) if light is not None else np.zeros(SH_FLOATS, np.float32)
        self.gpu.device.queue.write_buffer(self.ubuf, 0, u.tobytes())

    def _depth_state(self):
        # reversed depth: the nearest (largest z) wins. The game's caster pipelines (capture s2: NegativeDirectionalLight
        # eid 8208, NegativeLightVFX eid 9295) have no depth bias, depth clamp on and no culling; the receivers' 0.05
        # normal offset is the only bias. Depth clamp: emulated in the caster shaders (frag_depth = clamped slice z, clip z
        # kept inside the volume), so it needs no WebGPU feature.
        return {"format": "depth32float", "depth_write_enabled": True, "depth_compare": "greater"}

    def _model_pipe(self):
        if self._p_model is None:
            g = self.gpu
            from . import gpu as G
            mod = g.device.create_shader_module(code=CASTER_WGSL)
            A = g.wgpu.VertexFormat
            vb = [{"array_stride": G.FLOATS * 4, "step_mode": "vertex", "attributes": [
                {"format": A.float32x3, "offset": 0, "shader_location": 0},
                {"format": A.float32x2, "offset": 40, "shader_location": 3}]}]
            self._p_model = g.device.create_render_pipeline(
                layout=g.device.create_pipeline_layout(bind_group_layouts=[g.bgl, self.bgl_c]),
                vertex={"module": mod, "entry_point": "vs_cast", "buffers": vb},
                primitive={"topology": "triangle-list", "cull_mode": "none"},
                depth_stencil=self._depth_state(), multisample={"count": 1},
                fragment={"module": mod, "entry_point": "fs_cast", "targets": []})
        return self._p_model

    def _fx_pipe(self, fxr):
        key = id(fxr)
        if key not in self._fx:
            from ..fx import packers as pk
            from ..fx import render as fr
            g = self.gpu
            src = patch(fr.SHADER.read_text(encoding="utf-8"))
            # vs_shadow_fx wraps vs_fx (depth clamp): WGSL cannot call an entry point, so vs_fx loses @vertex here
            src, k = re.subn(r"@vertex\s+fn vs_fx\(", "fn vs_fx(", src, count=1)
            if not k:
                raise ValueError("fx.wgsl: no '@vertex fn vs_fx(' to wrap")
            src += FX_CASTER_WGSL
            mod = g.device.create_shader_module(code=src)
            A = g.wgpu.VertexFormat
            attrs, off = [], 0
            for loc, (_n, k) in enumerate(pk.LAYOUT):
                attrs.append({"format": {3: A.float32x3, 4: A.float32x4}[k], "offset": off * 4, "shader_location": loc})
                off += k
            vb = [{"array_stride": pk.FLOATS * 4, "step_mode": "vertex", "attributes": attrs}]
            U = g.wgpu.TextureUsage
            z = g.device.create_texture(size=(1, 1, 1), format="r32float", usage=U.TEXTURE_BINDING)
            p = g.device.create_render_pipeline(
                layout=g.device.create_pipeline_layout(bind_group_layouts=[fxr.bgl]),
                vertex={"module": mod, "entry_point": "vs_shadow_fx", "buffers": vb},
                primitive={"topology": "triangle-list", "cull_mode": "none"},
                depth_stencil=self._depth_state(), multisample={"count": 1},
                fragment={"module": mod, "entry_point": "fs_shadow_fx", "targets": []})
            self._fx[key] = (p, z.create_view())
        return self._fx[key]

    @staticmethod
    def _casts(draw, slice_casters) -> bool:
        if getattr(draw, "terrain", None):
            return False
        cls = getattr(draw, "light_class", "unit") or "unit"
        return ("world" if cls == "world" else "unit") in slice_casters

    def encode(self, enc, prep, light, overlay=None):
        """Render the casters of `prep` (gpu.Prepared) and `overlay` (fx/render.FxOverlay: draws with `cast` set) into the
        slices when `light` has shadows on (else the receivers read 'off'). Returns the Spec (None: off)."""
        pts = [dr.world.reshape(-1, 3) for dr in prep.draws if not getattr(dr, "terrain", None)]
        fxd = [d for d in (getattr(overlay, "draws", None) or []) if getattr(d, "cast", None) is not None]
        pts += [d.verts[:, 0:3] for d in fxd]
        P = np.concatenate(pts) if pts else np.zeros((1, 3))
        lo, hi = P.min(0), P.max(0)
        centre = ((lo[0] + hi[0]) / 2.0, max(lo[1], min(0.0, hi[1])), (lo[2] + hi[2]) / 2.0)
        spec = spec_for(light, centre)
        if spec is None:
            self.off(light)
            return None
        self._alloc(spec.size, int(LOCAL["spot_size"]) if any(r["kind"] == "spot" and r["shadow"] >= 0
                                                              for r in spec.locals) else None,
                    int(LOCAL["cube_size"]) if any(r["kind"] == "cube" for r in spec.locals) else None)
        self.spec = spec
        dev = self.gpu.device
        dev.queue.write_buffer(self.ubuf, 0, spec.uniforms().tobytes())
        mats = spec.matrices()
        counts = []
        for k, (s, M) in enumerate(zip(spec.slices, mats)):
            dev.queue.write_buffer(self.cbufs[k], 0, np.asarray(M.T, np.float32).tobytes())
            rp = enc.begin_render_pass(color_attachments=[], depth_stencil_attachment={
                "view": self.tex.create_view(dimension="2d", base_array_layer=k, array_layer_count=1),
                "depth_load_op": "clear", "depth_store_op": "store", "depth_clear_value": 0.0})
            n = 0
            models = [dr for dr in prep.draws if self._casts(dr, s.casters) and dr.blend <= 0.0]
            if models:
                rp.set_pipeline(self._model_pipe())
                rp.set_bind_group(1, self.cbinds[k])
                for dr in models:
                    rp.set_bind_group(0, dr.bind)
                    rp.set_vertex_buffer(0, dr.vbuf)
                    rp.draw(dr.count)
                    n += 1
            if fxd and "fx" in s.casters:
                p, zview = self._fx_pipe(overlay.fxr)
                rp.set_pipeline(p)
                for d in fxd:
                    b = self._fx_bind(overlay, d, k, M, zview)
                    rp.set_bind_group(0, b)
                    rp.set_vertex_buffer(0, d.gpu["vbuf"])
                    rp.draw(len(d.verts))
                    n += 1
            rp.end()
            counts.append(n)
        self.stats = {"casters": dict(zip([s.name for s in spec.slices], counts)), "centre": list(spec.centre)}
        self.stats.update(self._encode_local(enc, prep, spec))
        return spec

    def _encode_local(self, enc, prep, spec) -> dict:
        """The spot maps (static world casters only, as the game's `Shadows / SpotLights` pass in the r3 capture) and the
        shadowed point's cube (world and unit casters: no capture shows which; the game passes cube index -1 to every
        shadowed point light seen so far)."""
        dev = self.gpu.device
        out = {}
        world = [dr for dr in prep.draws if self._casts(dr, ("world",)) and dr.blend <= 0.0]
        for r in spec.locals:
            if r["kind"] != "spot" or r["shadow"] < 0:
                continue
            i = r["shadow"]
            dev.queue.write_buffer(self.cbufs[MAX_SLICES + i], 0, np.asarray(r["M"].T, np.float32).tobytes())
            rp = enc.begin_render_pass(color_attachments=[], depth_stencil_attachment={
                "view": self.spot_tex.create_view(dimension="2d", base_array_layer=i, array_layer_count=1),
                "depth_load_op": "clear", "depth_store_op": "store", "depth_clear_value": 0.0})
            if world:
                rp.set_pipeline(self._model_pipe())
                rp.set_bind_group(1, self.cbinds[MAX_SLICES + i])
                for dr in world:
                    rp.set_bind_group(0, dr.bind)
                    rp.set_vertex_buffer(0, dr.vbuf)
                    rp.draw(dr.count)
            rp.end()
            out.setdefault("spot_casters", {})[r["name"] or str(i)] = len(world)
        cube = next((r for r in spec.locals if r["kind"] == "cube"), None)
        if cube is not None:
            # the scene's placed objects too (`--depth-unit` occluders: the cave shrine shadows the player's light)
            cast = [dr for dr in list(prep.draws) + list(getattr(prep, "occluders", None) or [])
                    if not getattr(dr, "terrain", None) and dr.blend <= 0.0]
            lp = np.asarray(cube["pos"], float)
            dev.queue.write_buffer(self.lbuf, 0, np.array([*lp, LOCAL["cube_bias"], 1.0 / LOCAL["cube_range"], 0, 0, 0],
                                                          np.float32).tobytes())
            for f, M in enumerate(cube_face_matrices(lp)):
                b = MAX_SLICES + MAX_SPOT_MAPS + f
                dev.queue.write_buffer(self.cbufs[b], 0, np.asarray(M.T, np.float32).tobytes())
                rp = enc.begin_render_pass(color_attachments=[], depth_stencil_attachment={
                    "view": self.cube_tex.create_view(dimension="2d", base_array_layer=f, array_layer_count=1),
                    "depth_load_op": "clear", "depth_store_op": "store", "depth_clear_value": 1.0})
                if cast:
                    rp.set_pipeline(self._cube_pipe())
                    rp.set_bind_group(1, self.cbinds[b])
                    rp.set_bind_group(2, self.lbind)
                    for dr in cast:
                        rp.set_bind_group(0, dr.bind)
                        rp.set_vertex_buffer(0, dr.vbuf)
                        rp.draw(dr.count)
                rp.end()
            out["cube_casters"] = len(cast)
        return out

    def _cube_pipe(self):
        if self._p_cube is None:
            g = self.gpu
            from . import gpu as G
            mod = g.device.create_shader_module(code=CASTER_WGSL)
            A = g.wgpu.VertexFormat
            vb = [{"array_stride": G.FLOATS * 4, "step_mode": "vertex", "attributes": [
                {"format": A.float32x3, "offset": 0, "shader_location": 0},
                {"format": A.float32x2, "offset": 40, "shader_location": 3}]}]
            self._p_cube = g.device.create_render_pipeline(
                layout=g.device.create_pipeline_layout(bind_group_layouts=[g.bgl, self.bgl_c, self.bgl_l]),
                vertex={"module": mod, "entry_point": "vs_cast", "buffers": vb},
                primitive={"topology": "triangle-list", "cull_mode": "none"},
                depth_stencil={"format": "depth32float", "depth_write_enabled": True, "depth_compare": "less"},
                multisample={"count": 1}, fragment={"module": mod, "entry_point": "fs_cast_cube", "targets": []})
        return self._p_cube

    def _fx_bind(self, overlay, d, k, M, zview):
        from ..fx import render as fr
        g = self.gpu
        if "vbuf" not in d.gpu:
            overlay._bind(d, zview)
        key = ("shadow", k)
        if key not in d.gpu:
            ub = g.device.create_buffer(size=fr.UFLOATS * 4, usage=g.wgpu.BufferUsage.UNIFORM | g.wgpu.BufferUsage.COPY_DST)
            v = d.gpu["views"]
            d.gpu[key] = (ub, g.device.create_bind_group(layout=overlay.fxr.bgl, entries=[
                {"binding": 0, "resource": {"buffer": ub, "offset": 0, "size": fr.UFLOATS * 4}},
                {"binding": 1, "resource": overlay.fxr.s_rep}, {"binding": 2, "resource": overlay.fxr.s_clamp},
                *[{"binding": 3 + s, "resource": v[s]} for s in range(11)],
                {"binding": 14, "resource": zview}]))
        ub, bind = d.gpu[key]

        class _Cam:                               # the uniforms' camera terms: towards the light (Fresnel, facing)
            @staticmethod
            def basis():
                f = np.asarray(self.spec.slices[k].dir, float)
                return (np.zeros(3), np.zeros(3), f / max(np.linalg.norm(f), 1e-9))
        u = overlay.uniforms(d, M, (1, 1), (0.0, 1.0), getattr(overlay, "cam", None) or _Cam)
        u[21] = 0.0                               # no soft-particle fade against the light's depth
        u[53] = float(d.cast)                     # pad0.y: the threshold
        g.device.queue.write_buffer(ub, 0, u.tobytes())
        return bind

    # ---- the ground of a game shot ------------------------------------------------------------------------------------
    def ground_gain(self, light, cam, origin, size, y=0.0) -> np.ndarray | None:
        """(H, W, 3) factor for a game shot's ground (plane y): (ambient + shadowed direct) / (ambient + direct), with the
        shadows of the last `encode`; None when off. Irradiance terms: Light.game's SH at the up normal x IBL, its
        directional lights (colour x diffuse weight / pi x N.L) and local lights; else the lit model's ambient, key and
        points."""
        if self.spec is None:
            return None
        return self._ground_pass("fs_ground", light, cam, origin, size, y)

    def ground_vis(self, light, cam, origin, size, y=0.0, *, surface=None) -> np.ndarray | None:
        """(H, W, 4) the ground's shadow terms on the plane y, with the shadows of the last `encode`: G (the global
        slice at its strength), slice 0 and slice 1 visibility (1 lit), the shadowed point's cube (1 lit, 1 without
        one); None when off. `surface=(P, n)` provides the plate's per-pixel world positions and normals instead of
        the ground plane. GameLighting.irradiance composes these terms with the scene's lights."""
        if self.spec is None:
            return None
        entry = "fs_vis" if surface is None else "fs_surface_vis"
        return self._ground_pass(entry, light, cam, origin, size, y, 4, surface)

    def _ground_pass(self, entry, light, cam, origin, size, y, nc=3, surface=None):
        g = self.gpu
        wgpu = g.wgpu
        dev = g.device
        if self._ground is None:
            self._ground = {}
        if entry not in self._ground:
            mod = dev.create_shader_module(code=RECEIVER_WGSL + GROUND_WGSL)
            layout = [{"binding": 0, "visibility": wgpu.ShaderStage.FRAGMENT, "buffer": {"type": "uniform"}}]
            if surface is not None:
                layout.append({"binding": 1, "visibility": wgpu.ShaderStage.FRAGMENT,
                               "buffer": {"type": "read-only-storage"}})
            bgl0 = dev.create_bind_group_layout(entries=layout)
            p = dev.create_render_pipeline(
                layout=dev.create_pipeline_layout(bind_group_layouts=[bgl0, self.bgl]),
                vertex={"module": mod, "entry_point": "vs_full"}, primitive={"topology": "triangle-list"},
                fragment={"module": mod, "entry_point": entry, "targets": [{"format": "rgba32float"}]})
            ub = dev.create_buffer(size=GROUND_FLOATS * 4, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
            bindings = [{"binding": 0, "resource": {"buffer": ub, "offset": 0, "size": GROUND_FLOATS * 4}}]
            b0 = dev.create_bind_group(layout=bgl0, entries=bindings) if surface is None else None
            self._ground[entry] = (p, ub, bgl0, bindings, b0)
        p, ub, bgl0, bindings, b0 = self._ground[entry]
        W, H = size
        uniforms = ground_uniforms(light, cam, origin, y)
        if surface is not None:
            P, n = surface
            receivers = np.empty((H, W, 4), np.float32)
            receivers[..., :3], receivers[..., 3] = n, P @ cam.basis()[2]
            buf = dev.create_buffer_with_data(data=receivers, usage=wgpu.BufferUsage.STORAGE)
            b0 = dev.create_bind_group(layout=bgl0, entries=[
                *bindings, {"binding": 1, "resource": {"buffer": buf, "offset": 0, "size": receivers.nbytes}}])
            uniforms[11] = W
        dev.queue.write_buffer(ub, 0, uniforms.tobytes())
        wp = -(-W // 64) * 64
        tex = dev.create_texture(size=(wp, H, 1), format="rgba32float",
                                 usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
        enc = dev.create_command_encoder()
        rp = enc.begin_render_pass(color_attachments=[{"view": tex.create_view(), "load_op": "clear", "store_op": "store",
                                                       "clear_value": (1, 1, 1, 1)}])
        rp.set_pipeline(p)
        rp.set_scissor_rect(0, 0, W, H)
        rp.set_bind_group(0, b0)
        rp.set_bind_group(1, self.bind)
        rp.draw(3)
        rp.end()
        dev.queue.submit([enc.finish()])
        data = dev.queue.read_texture({"texture": tex, "mip_level": 0, "origin": (0, 0, 0)},
                                      {"offset": 0, "bytes_per_row": wp * 16, "rows_per_image": H}, (wp, H, 1))
        return np.frombuffer(data, np.float32).reshape(H, wp, 4)[:, :W, :nc].copy()


GROUND_FLOATS = 4 * (5 + 4 + 4 + 8 + 8)


def terrain_dirs(gm: dict) -> list:
    """The directional lights the game's terrain gets (lane r3-shadows2, s2 capture: the terrain draw 13692 lists only the
    moon, shadow index 0, while the unit draw 13030 also has the player key light x 30 and the fill x 4, which are unit
    lights): Light.game `terrain_dirs` (indices into dirs) when given, else the lights with shadow index 0."""
    dirs = list(gm.get("dirs") or [])
    if gm.get("terrain_dirs") is not None:
        return [dirs[i] for i in gm["terrain_dirs"] if 0 <= int(i) < len(dirs)]
    return [d for d in dirs if int(d.get("shadow_mode", -1)) == 0] or dirs[:1]


def ground_terms(light) -> tuple:
    """(ambient rgb, [(dir, rgb, shadow index)], [point dicts]) irradiance terms of a gpu.Light on an up-facing ground:
    with Light.game the terrain's own light set (terrain_dirs), else the lit model's key light."""
    gm = getattr(light, "game", None) or {}
    if gm:
        sh = np.asarray(gm.get("sh") or np.zeros((9, 3)), float).reshape(9, 3)
        ibl = gm.get("ibl") or {}
        up = sh[0] + sh[1] - sh[6] + (-sh[8])           # 1, y, 3 z^2 - 1 at z 0 = -1, x^2 - y^2 = -1 (n = (0, 1, 0))
        amb = np.maximum(up, 0.0) / np.pi * float(ibl.get("intensity", 1.0)) * float(ibl.get("diffuse", 1.0))
        dirs = [(np.asarray(d["dir"], float), np.asarray(d["color"], float) * float(d.get("diffuse", 1.0)) / np.pi,
                 int(d.get("shadow_mode", -1)) if 0 <= int(d.get("shadow_mode", -1)) < MAX_SLICES else -1)
                for d in terrain_dirs(gm)][:4]
        from . import gpu as G
        pts = [dict(p, color=list(np.asarray(p["color"], float) / np.pi)) for p in
               list(G.game_points(gm)) + [q for q in (light.points or ()) if not q.get("directional")]]
        return amb, dirs, pts
    return (np.asarray(light.ambient, float), [(np.asarray(light.key_dir, float), np.asarray(light.key_col, float), 0)],
            [p for p in (light.points or ()) if not p.get("directional")])


def ground_uniforms(light, cam, origin, y) -> np.ndarray:
    u = np.zeros(GROUND_FLOATS, np.float32)
    r, up, w = (np.asarray(v, float) for v in cam.basis())
    amb, dirs, pts = ground_terms(light)
    pts = pts[:8]
    u[0:3], u[3] = r, cam.k
    u[4:7], u[7] = up, y
    u[8:11] = w
    u[12:16] = (origin[0], origin[1], len(dirs), len(pts))
    u[16:19] = amb
    for k, (d, c, si) in enumerate(dirs):
        u[20 + 4 * k:23 + 4 * k] = d / max(np.linalg.norm(d), 1e-9)
        u[23 + 4 * k] = si
        u[36 + 4 * k:39 + 4 * k] = c
    for k, p in enumerate(pts):
        u[52 + 4 * k:55 + 4 * k] = p["pos"]
        u[55 + 4 * k] = p["radius"]
        u[84 + 4 * k:87 + 4 * k] = p["color"]
        u[87 + 4 * k] = p.get("atten", 1.0)
    return u
