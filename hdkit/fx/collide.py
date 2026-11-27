"""The game's particle collision query: `scene.intersect` / `scene.intersectExt` as D2R.exe runs them (lane r3-intersect).

Read from D2R.exe statics (`docs/hdfx-vm-re.md`, section "scene.intersect: what the game does"; RVAs there):

- The kernels (0xbf18d0 intersect, 0xbf2230 intersectExt) build a PopcornFX ray packet per 128 particles (0xbf5070):
  origins, (direction xyz, length) when |direction|^2 > 1e-12 else zeros, hit distances initialised to the lengths, and
  call the scene interface's RayTracePacket. D2R's one (vtable 0x1d672b8 slot 1 = 0x7e80c0, `PopcornPhysics::
  RayTracePacket`, D2Physics.cpp) casts every ray through the game's physics world (dmWorld, 0x98e8f0):
  * start = origin, **end = origin + direction** (the length is not used: scripts pass a unit direction, so the game's
    ray is 1 world unit long whatever length the script asks for), a sphere sweep of radius 0.01 (no sweep radii given);
  * the query filter is entry min(bsf(mask), 6) of the table D2Physics fills at start (0x7e8790 -> +0xe0):
    0 effectenvironment, 1 player, 2 monster, 3 damage, 4 effectfloor, 5 wall, 6 all. Mask 0 (what almost every effect
    passes) makes `bsf` leave its register unchanged: the stale value (a ray count or pointer bits) is >= 6 in practice,
    so mask 0 queries "all" (`MASK0_INDEX`);
  * on a hit it writes the hit normal and **distance 0** (not the hit distance); a miss leaves the distance at the length.
- Back in the kernel a row is a hit when distance < length (so a hit with length <= 0 is a miss): intersect returns
  float4 (normal, 0), a miss (1, 0, 0, +inf); intersectExt returns the raw int4 (normal.x, normal.y, the distance (0)
  with normal.z's sign bit, -1) and a miss (1.0, 0, +inf, 0) as bits; scripts rebuild normal.z = +-sqrt(1 - x^2 - y^2).
  With a constant false "enabled" argument or without a scene the result is all zeros.

So in the game a particle collides when a surface lies within 1 world unit (half a sub-tile) ahead along its motion, and
the physics template then puts it back at its previous position (distance 0 = fraction 0) with its velocity bounced.
`World.cast` is the physics query (closest hit of a thin sphere sweep against boxes, spheres, half-spaces and triangle
meshes, each with D2Physics filter category bits); `intersect` / `intersect_ext` wrap it with the kernel's semantics.
**Confirmed in game (lane r3-intersect2, RenderDoc captures, billboard vertices = world positions):** Cold Blood's ice
chunks (`fx_ice_freeze_explode_center`, 108 = 9 explosions x 12) rest 0.71-1.32 units above the ground (middle value 1.04;
1.02-1.06 within 1.5 units of the measured ground point) and the barricade wall's debris 0.80-1.19; the authored reading
would put them at 0.01. The look-ahead is per frame: debris faster than ~1 unit per frame passes the floor between two
queries (tunnels at 30 fps where 60 fps catches it; tests/test_hdfx_collide_rate.py).
`mode="authored"` instead uses the script's length and the real hit distance (what PopcornFX's own scene would return;
for comparisons only).

What is in the game's physics world (D2Physics, LevelTranslationLayer 0x6b3150): per room a floor slab ("default_floor",
category floor, top at the ground, 10 units deep) unless the room's preset has its own floor bodies; the static bodies of
the scene's entities and prefabs (`PhysicsBodyDefinitionComponent`: boxes, spheres, `.physics` files; filter wall,
walltransparent, floor, destructible, ...), kinematic spheres of a few critters' unit JSONs. `world_from_preset` reads a
preset JSON's bodies (boxes and spheres; `.physics` files are counted in `World.skipped`).
"""
from __future__ import annotations

import json
import math

import numpy as np

F32 = np.float32

# D2Physics filter categories (D2R.exe 0x7e8790: name -> bit)
CAT = {"default": 0x1, "floor": 0x2, "wall": 0x4, "player": 0x8, "monster": 0x10, "missile": 0x20, "item": 0x40,
       "object": 0x80, "simworld": 0x100, "destructible": 0x200, "walltransparent": 0x400, "effectfloor": 0x800}
ALL = 0xffff
# what each body filter collides with (its mask list in 0x7e8790), for the queries named after body filters
_BODY_MASK = {
    "player": ("wall", "floor", "player", "object", "destructible", "walltransparent"),
    "monster": ("wall", "floor", "player", "monster", "destructible", "walltransparent"),
}


def _bits(*names) -> int:
    b = 0
    for n in names:
        b |= CAT[n]
    return b


# the query table D2Physics+0xe0 (index = min(bsf(mask), 6)): name, categories it hits
QUERIES = [
    ("effectenvironment", _bits("floor", "effectfloor", "wall", "walltransparent")),
    ("player", _bits(*_BODY_MASK["player"])),
    ("monster", _bits(*_BODY_MASK["monster"])),
    ("damage", _bits("player", "monster")),
    ("effectfloor", _bits("floor", "effectfloor")),
    ("wall", _bits("wall", "walltransparent")),
    ("all", ALL),
]
MASK0_INDEX = 6                 # bsf(0): register unchanged, stale value >= 6 in practice -> "all"
SWEEP_RADIUS = 0.01             # 0x7e80c0: no sweep radii in PopcornFX's packet -> 0.01
MIN_DIR2 = 1e-12                # 0xbf5070: directions with |d|^2 <= this are zeroed
NO_HIT = (1.0, 0.0, 0.0, math.inf)


def query_index(mask: int) -> int:
    mask = int(mask) & 0xffffffff
    if mask == 0:
        return MASK0_INDEX
    return min((mask & -mask).bit_length() - 1, 6)


def query_categories(mask: int) -> int:
    return QUERIES[query_index(mask)][1]


# ---- shapes ------------------------------------------------------------------------------------------------------------------

def _quat_matrix(q) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float64)


class Box:
    """Oriented box: world centre, rotation (3x3, columns = local axes in world) and half extents."""
    kind = "box"

    def __init__(self, center, rot, extents, cat: int = CAT["wall"], name: str = ""):
        self.c = np.asarray(center, np.float64).reshape(3)
        self.R = np.asarray(rot, np.float64).reshape(3, 3)
        self.e = np.abs(np.asarray(extents, np.float64).reshape(3))
        self.cat, self.name = int(cat), name

    def cast(self, p0, p1, r):
        """-> (fraction (N,), normal (N,3)); fraction inf where no hit. Rays starting inside are not hits."""
        a = (p0 - self.c) @ self.R
        b = (p1 - self.c) @ self.R
        d = b - a
        e = self.e + r
        inside = np.all(np.abs(a) < e, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / d
            t1, t2 = (-e - a) * inv, (e - a) * inv
        lo, hi = np.minimum(t1, t2), np.maximum(t1, t2)
        par = d == 0                                     # parallel: inside the slab or never
        ok_par = np.abs(a) <= e
        lo = np.where(par, np.where(ok_par, -np.inf, np.inf), lo)
        hi = np.where(par, np.where(ok_par, np.inf, -np.inf), hi)
        tn, ax = lo.max(axis=1), lo.argmax(axis=1)
        tf = hi.min(axis=1)
        hit = (tn <= tf) & (tn >= 0) & (tn <= 1) & ~inside
        n = np.zeros_like(a)
        rows = np.arange(len(a))
        n[rows, ax] = -np.sign(d[rows, ax])
        return np.where(hit, tn, np.inf), n @ self.R.T


class Sphere:
    kind = "sphere"

    def __init__(self, center, radius, cat: int = CAT["monster"], name: str = ""):
        self.c = np.asarray(center, np.float64).reshape(3)
        self.r = float(radius)
        self.cat, self.name = int(cat), name

    def cast(self, p0, p1, r):
        d = p1 - p0
        m = p0 - self.c
        R = self.r + r
        A = (d * d).sum(1)
        B = (m * d).sum(1)
        C = (m * m).sum(1) - R * R
        disc = B * B - A * C
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (-B - np.sqrt(np.maximum(disc, 0))) / A
        hit = (A > 0) & (disc >= 0) & (C > 0) & (t >= 0) & (t <= 1)
        q = m + d * np.where(hit, t, 0)[:, None]
        nl = np.linalg.norm(q, axis=1, keepdims=True)
        n = np.where(nl > 0, q / np.where(nl > 0, nl, 1), 0)
        return np.where(hit, t, np.inf), n


class HalfSpace:
    """Solid below the plane y = height (the default floor slab, its 10-unit depth and room extent not needed here):
    `extent` (x0, z0, x1, z1) limits it to a room's rectangle when given."""
    kind = "floor"

    def __init__(self, height: float = 0.0, cat: int = CAT["floor"], extent=None, depth: float = 10.0, name: str = ""):
        self.h, self.depth = float(height), float(depth)
        self.extent = None if extent is None else tuple(float(v) for v in extent)
        self.cat, self.name = int(cat), name

    def cast(self, p0, p1, r):
        top = self.h + r
        y0, y1 = p0[:, 1], p1[:, 1]
        dy = y1 - y0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (top - y0) / dy
        inside = (y0 < top) & (y0 > self.h - self.depth - r)
        hit = (dy < 0) & (y0 >= top) & (t >= 0) & (t <= 1) & ~inside
        if self.extent is not None:
            x0, z0, x1, z1 = self.extent
            q = p0 + (p1 - p0) * np.where(hit, t, 0)[:, None]
            hit &= (q[:, 0] >= x0) & (q[:, 0] <= x1) & (q[:, 2] >= z0) & (q[:, 2] <= z1)
        n = np.zeros_like(p0)
        n[:, 1] = 1.0
        return np.where(hit, t, np.inf), n


class Mesh:
    """Triangle mesh (world positions (V,3), indices (T,3)); both sides collide, the normal faces the ray."""
    kind = "mesh"

    def __init__(self, positions, indices, cat: int = CAT["wall"], name: str = ""):
        P = np.asarray(positions, np.float64).reshape(-1, 3)
        I = np.asarray(indices, np.int64).reshape(-1, 3)
        self.v0, self.e1, self.e2 = P[I[:, 0]], P[I[:, 1]] - P[I[:, 0]], P[I[:, 2]] - P[I[:, 0]]
        self.cat, self.name = int(cat), name

    def cast(self, p0, p1, r, chunk: int = 4096):
        n_rays = len(p0)
        best = np.full(n_rays, np.inf)
        nrm = np.zeros((n_rays, 3))
        d = p1 - p0
        for s in range(0, len(self.v0), chunk):
            v0, e1, e2 = self.v0[s:s + chunk], self.e1[s:s + chunk], self.e2[s:s + chunk]
            pv = np.cross(d[:, None, :], e2[None])                     # (N,T,3)
            det = (e1[None] * pv).sum(-1)
            with np.errstate(divide="ignore", invalid="ignore"):
                inv = 1.0 / det
                tv = p0[:, None, :] - v0[None]
                u = (tv * pv).sum(-1) * inv
                qv = np.cross(tv, e1[None])
                v = (d[:, None, :] * qv).sum(-1) * inv
                t = (e2[None] * qv).sum(-1) * inv
            ok = (np.abs(det) > 1e-12) & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
            t = np.where(ok, t, np.inf)
            k = t.argmin(1)
            tk = t[np.arange(n_rays), k]
            better = tk < best
            if better.any():
                fn = np.cross(e1[k], e2[k])
                fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-30)
                fn = np.where(((fn * d).sum(1) > 0)[:, None], -fn, fn)
                best = np.where(better, tk, best)
                nrm = np.where(better[:, None], fn, nrm)
        return best, nrm


# ---- the world ---------------------------------------------------------------------------------------------------------------

class World:
    """The physics bodies particles collide with (the game's D2Physics world, as far as the viewer knows the scene)."""

    def __init__(self, shapes=None, mode: str = "game"):
        self.shapes = list(shapes or [])
        self.mode = mode                      # "game" (D2R.exe's behaviour) or "authored" (length and real distance)
        self.skipped: dict = {}               # shapes not modelled (e.g. ".physics" files) -> count
        self.notes: list = []

    def add(self, shape):
        self.shapes.append(shape)
        return shape

    def cast(self, p0, p1, cats: int = ALL, radius: float = SWEEP_RADIUS):
        """Closest hit of sweeps p0 -> p1 (N,3) against the shapes whose category is in `cats`:
        -> (hit (N,) bool, fraction (N,), normal (N,3) float64)."""
        p0 = np.asarray(p0, np.float64).reshape(-1, 3)
        p1 = np.asarray(p1, np.float64).reshape(-1, 3)
        best = np.full(len(p0), np.inf)
        nrm = np.zeros((len(p0), 3))
        for s in self.shapes:
            if not (s.cat & cats):
                continue
            t, n = s.cast(p0, p1, radius)
            b = t < best
            best = np.where(b, t, best)
            nrm = np.where(b[:, None], n, nrm)
        return np.isfinite(best), best, nrm


def floor_world(height: float = 0.0, mode: str = "game") -> World:
    """A room's default floor slab and nothing else (what an effect on open ground collides with)."""
    return World([HalfSpace(height, name="default_floor")], mode=mode)


def _rows(*arrs):
    return max(len(a) for a in arrs)


def _trace(world: World, o, d, length, mask):
    o = np.asarray(o, np.float64).reshape(-1, 3)
    d = np.asarray(d, np.float64).reshape(-1, 3)
    ln = np.asarray(length, np.float64).reshape(-1)
    n = _rows(o, d, ln)
    o, d, ln = np.broadcast_to(o, (n, 3)), np.broadcast_to(d, (n, 3)), np.broadcast_to(ln, (n,))
    d = np.where(((d * d).sum(1) > MIN_DIR2)[:, None], d, 0.0)
    cats = query_categories(mask)
    if world.mode == "authored":
        hit, frac, nrm = world.cast(o, o + d * ln[:, None], cats)
        dist = frac * ln
        return hit & (dist < ln), nrm, np.where(hit, dist, ln)
    hit, _frac, nrm = world.cast(o, o + d, cats)
    return hit & (0.0 < ln), nrm, np.zeros(n)


def intersect(world: World | None, o, d, length, enabled=True, mask: int = 0) -> np.ndarray:
    """scene.intersect: (N,4) float32 (hit normal, distance) per ray; miss (1, 0, 0, +inf); no world or a false constant
    `enabled`: zeros."""
    n = _rows(np.asarray(o).reshape(-1, 3), np.asarray(d).reshape(-1, 3), np.asarray(length).reshape(-1))
    if world is None or not np.any(enabled):
        return np.zeros((n, 4), F32)
    hit, nrm, dist = _trace(world, o, d, length, mask)
    out = np.empty((len(hit), 4), F32)
    out[:] = NO_HIT
    out[hit, :3] = nrm[hit]
    out[hit, 3] = dist[hit]
    return out


def intersect_ext(world: World | None, o, d, length, enabled=True, mask: int = 0) -> np.ndarray:
    """scene.intersectExt: (N,4) int32 raw bits: hit (normal.x, normal.y, |distance| with normal.z's sign, -1), miss
    (1.0, 0, +inf, 0); no world or a false constant `enabled`: zeros."""
    n = _rows(np.asarray(o).reshape(-1, 3), np.asarray(d).reshape(-1, 3), np.asarray(length).reshape(-1))
    if world is None or not np.any(enabled):
        return np.zeros((n, 4), np.int32)
    hit, nrm, dist = _trace(world, o, d, length, mask)
    f = np.zeros((len(hit), 4), F32)
    f[:, 0], f[:, 2] = 1.0, np.inf                        # miss
    bits = f.view(np.int32)
    nb = nrm.astype(F32).view(np.int32)
    db = np.abs(dist).astype(F32).view(np.int32)
    bits[hit, 0] = nb[hit, 0]
    bits[hit, 1] = nb[hit, 1]
    bits[hit, 2] = (nb[hit, 2] & np.int32(-0x80000000)) | (db[hit] & 0x7fffffff)
    bits[hit, 3] = -1
    return bits


# ---- bodies from scene JSONs ---------------------------------------------------------------------------------------------------

def _components(ent: dict) -> dict:
    out = {}
    for c in ent.get("components", []):
        out.setdefault(c.get("type"), c)
    return out


def _filter_cat(name) -> int:
    return CAT.get(str(name or "default").lower(), CAT["default"])


def bodies(world: World, comp: dict, M: np.ndarray) -> int:
    """Add the fixtures of one PhysicsBodyDefinitionComponent placed by world matrix M (4x4, may scale); -> count added."""
    cat = _filter_cat(comp.get("filter"))
    M = np.asarray(M, np.float64)
    L = M[:3, :3]
    sc = np.linalg.norm(L, axis=0)
    Rm = L / np.where(sc > 0, sc, 1)
    k = 0
    for fx in comp.get("fixturedefs", []):
        st = fx.get("shapetype") or {}
        t = st.get("type")
        c = st.get("center") or {}
        cl = np.array([c.get("x", 0.0), c.get("y", 0.0), c.get("z", 0.0)], np.float64)
        cw = M[:3, :3] @ cl + M[:3, 3]
        if t == "PhysicsBoxDefinition":
            q = st.get("orientation") or {}
            Rl = _quat_matrix((q.get("x", 0), q.get("y", 0), q.get("z", 0), q.get("w", 1)))
            e = st.get("extents") or {}
            ext = np.array([e.get("x", 0.0), e.get("y", 0.0), e.get("z", 0.0)], np.float64) * sc
            world.add(Box(cw, Rm @ Rl, ext, cat, comp.get("name", "")))
            k += 1
        elif t == "PhysicsSphereDefinition":
            world.add(Sphere(cw, float(st.get("radius", 0.0)) * float(sc.max()), cat, comp.get("name", "")))
            k += 1
        else:
            world.skipped[t or "?"] = world.skipped.get(t or "?", 0) + 1
    return k


def world_from_preset(fs, preset: str, origin=None, mode: str = "game", ground_y: float = 0.0, prefabs: bool = True) -> World:
    """The physics world of one preset (a room): its entities' and prefab members' PhysicsBodyDefinitionComponents placed by
    their transforms (times `origin`, the room's world matrix), plus the default floor slab at `ground_y` when the preset
    has no floor body of its own (as LevelTranslationLayer does). `fs` reads logical paths (`fs.read(path) -> bytes`)."""
    from hdkit.view.preset import trs
    O = np.eye(4) if origin is None else np.asarray(origin, np.float64)
    d = json.loads(fs.read(preset).decode("utf-8-sig"))
    w = World(mode=mode)
    for ent in d.get("entities", []):
        c = _components(ent)
        M = O @ trs(c.get("TransformDefinitionComponent"))
        if "PhysicsBodyDefinitionComponent" in c:
            bodies(w, c["PhysicsBodyDefinitionComponent"], M)
        pf = c.get("PrefabPlacementDefinitionComponent")
        if pf and prefabs:
            try:
                sub = json.loads(fs.read(pf["prefab"]).decode("utf-8-sig"))
            except (OSError, KeyError, ValueError):
                w.skipped["prefab missing"] = w.skipped.get("prefab missing", 0) + 1
                continue
            for e2 in sub.get("entities", []):
                c2 = _components(e2)
                if "PhysicsBodyDefinitionComponent" in c2:
                    bodies(w, c2["PhysicsBodyDefinitionComponent"], M @ trs(c2.get("TransformDefinitionComponent")))
    if not any(s.cat & CAT["floor"] for s in w.shapes):
        w.add(HalfSpace(ground_y, name="default_floor"))
    return w
