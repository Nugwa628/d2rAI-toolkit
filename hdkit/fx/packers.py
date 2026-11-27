"""Packers: particles -> world-space triangles in the one vertex format of view/fx.wgsl (CPU, numpy, no GPU needed).

Billboard corner maths, the atlas frame rules, ribbon side vectors and the sort rule are ported from PopcornFX-Web
(AallynReed/PopcornFX-Web, MIT, `src/render/renderer.js` and `src/render/packer.js`; credit in hdkit/THIRD_PARTY.md),
which reads them from the PopcornFX runtime (CScreenBillboarderQuad, CAxialBillboarderQuad/Spheroidal/Capsule,
CPlanarBillboarderQuad, CRibbonBillboarder). Rewritten vectorised for an orthographic camera: the direction from the eye to
every particle is the camera's forward axis -w (camera.OrthoCamera.basis), so "eye" terms become constants.

Mode numbers are D2R's `General.BillboardingMode` (PopcornFX Editor.pkri enum order): 0 ScreenAligned, 1 ViewposAligned,
2 AxisAligned, 3 AxisAlignedSpheroid, 4 AxisAlignedCapsule, 5 PlaneAligned. Ribbon modes (`General.BillboardingMode` of a
ribbon): 0 ViewposAligned, 1 NormalAxisAligned, 2 SideAxisAligned. Size is a HALF extent, as in the runtime.
"""
from dataclasses import dataclass

import math

import numpy as np

# ---- vertex layout (floats) ----------------------------------------------------------------------------------------------
LAYOUT = (("pos", 3), ("nrm", 3), ("uv", 4), ("color", 4), ("misc", 4), ("so_base", 4), ("so_blend", 4), ("so_mask", 4),
          ("so_alpha", 4), ("so_disp", 4), ("sc", 4), ("dist", 4), ("so_db", 4), ("so_dm", 4), ("cur", 4), ("so_x", 4))
OFF = {}
_o = 0
for _n, _k in LAYOUT:
    OFF[_n] = (_o, _o + _k)
    _o += _k
FLOATS = _o                                   # 62 (16 vertex attributes, the wgpu limit)
PARTICLE_KEYS = [n for n, _k in LAYOUT if n not in ("pos", "nrm", "uv")]   # per-particle attributes copied to vertices
PARTICLE_FLOATS = sum(k for n, k in LAYOUT if n in PARTICLE_KEYS)
ID_SO = (1.0, 1.0, 0.0, 0.0)

# renderer input name -> (attribute, first component it fills); D2R Uber names (docs/hdfx-shaders.md 3.2), then standard
INPUT_MAP = {
    "Color.Color": ("color", 0), "Diffuse.Color": ("color", 0), "Color": ("color", 0),
    "Blend.BaseUVScaleAndOffset": ("so_base", 0), "Blend.BlendUVScaleAndOffset": ("so_blend", 0),
    "BlendMask.BlendMaskUVScaleAndOffset": ("so_mask", 0), "AlphaMask.AlphaMaskUVScaleAndOffset": ("so_alpha", 0),
    "VertexDisplacement.DisplacementUVScaleAndOffset": ("so_disp", 0),
    "UVDistortion.BlendDistortUVScaleAndOffset": ("so_db", 0), "UVDistortion.MaskDistortUVScaleAndOffset": ("so_dm", 0),
    "Blend.BaseContribution": ("sc", 0), "Blend.BlendContribution": ("sc", 1), "Exposure.ExposureMultiplier": ("sc", 2),
    "VertexDisplacement.DisplacementDistance": ("sc", 3),
    "UVDistortion.BlendDistortIntensity": ("dist", 0), "UVDistortion.MaskDistortIntensity": ("dist", 1),
    "Exposure.PhysicalIntensity": ("dist", 3),
    "AlphaRemap.AlphaCursor": ("cur", 0), "GradientRemap.GradientCursor": ("cur", 2),
    "Fresnel.FresnelCursor": ("misc", 2), "MonsterFade.Fade": ("misc", 3),
    "AlphaRemap.DissolveUVScaleAndOffset": ("so_x", 0),
    # lane mats (docs/hdfx-shaders.md 8-11): the other materials reuse attributes their family does not need
    "Diffuse.UVScaleAndOffset": ("so_base", 0),                                       # default mesh / billboard
    "Liquid.LiquidBaseUVScaleAndOffset": ("so_base", 0), "Liquid.CenterColor": ("color", 0),
    "Liquid.AlphaThreshold": ("so_blend", 0), "Liquid.LiquidHardness": ("so_blend", 1),
    "Liquid.EdgeHardness": ("so_blend", 2), "Liquid.EdgeControl": ("so_blend", 3),
    "LiquidColorMap.ColorMapUVScaleAndOffset": ("so_mask", 0),
    "Liquid.Roughness": ("so_alpha", 0), "Liquid.NormalIntensity": ("so_alpha", 1),
    "Liquid.SpeckleIntensity": ("so_alpha", 2), "Liquid.CubemapIntensity": ("so_alpha", 3),
    "Liquid.EdgeColor": ("so_dm", 0),
    "AlphaThreshold.AlphaThresholdMaskUVScaleAndOffset": ("so_base", 0),              # stencil
    "AlphaThreshold.AlphaThresholdRemapCursor": ("cur", 0),
}
# per family: inputs that land elsewhere than INPUT_MAP says (None = not drawn). Liquid (lane calib5, the game's liquid
# VS read from a capture, calib5 s3 eid 8331): its distortion is UVDistortion.Mask* only (T8 = MaskDistortUVScaleAndOffset,
# T9.y = MaskDistortIntensity; the Blend* pair is not read), so EdgeColor takes the blend-distortion UV slot `so_db`
# (it used to share `so_dm` with the mask-distortion UV, and whichever came last won)
FAMILY_INPUT_MAP = {"liquid": {"Liquid.EdgeColor": ("so_db", 0), "UVDistortion.BlendDistortUVScaleAndOffset": None,
                               "UVDistortion.BlendDistortIntensity": None}}
# per family: attributes whose defaults differ from the Uber ones (liquid scalars, colours)
FAMILY_DEFAULTS = {"liquid": {"so_blend": (0.0, 1.0, 1.0, 1.0), "so_alpha": (0.0, 1.0, 0.0, 0.25),
                              "so_db": (1.0, 1.0, 1.0, 0.0)}}
DEFAULTS = {"color": (1, 1, 1, 1), "misc": (0, 0, 0, 1), "so_base": ID_SO, "so_blend": ID_SO, "so_mask": ID_SO,
            "so_alpha": ID_SO, "so_disp": ID_SO, "sc": (1, 1, 1, 0), "dist": (0, 0, 0, 1), "so_db": ID_SO, "so_dm": ID_SO,
            "cur": (0, 0, 0, 0), "so_x": ID_SO}


def particle_block(inputs: dict, n: int, family: str | None = None) -> np.ndarray:
    """(n, PARTICLE_FLOATS) per-particle attributes from renderer inputs {name: (n, k) array, one row, or a scalar}; an
    input fills k components of its attribute from its first component on. Inputs a renderer does not have keep
    DEFAULTS (FAMILY_DEFAULTS of the material `family` first); unknown names are ignored. A 1-D array is one value per
    particle when n of them, else one vector for all."""
    dflt = {**DEFAULTS, **FAMILY_DEFAULTS.get(family or "", {})}
    blk = {k: np.tile(np.asarray(dflt[k], np.float32), (n, 1)) for k in PARTICLE_KEYS}
    fam_map = FAMILY_INPUT_MAP.get(family or "", {})
    for name, val in (inputs or {}).items():
        hit = fam_map[name] if name in fam_map else INPUT_MAP.get(name)
        if hit is None:
            continue
        key, at = hit
        v = np.asarray(val, np.float32)
        if v.ndim == 0:
            v = np.full((n, 1), float(v), np.float32)
        elif v.ndim == 1:
            v = v[:, None] if (len(v) == n and n > 1) or (len(v) == 1) else np.tile(v[None, :], (n, 1))
        if v.shape[0] != n:
            v = np.tile(v[:1], (n, 1))
        k = min(v.shape[1], 4 - at)
        blk[key][:, at:at + k] = v[:, :k]
    return np.concatenate([blk[k] for k in PARTICLE_KEYS], 1)


def _rows(x, n: int, k: int, fill: float) -> np.ndarray:
    """(n, k) from None (fill), a scalar, one row, or per-row values; short rows are padded with `fill` (a float3 scale
    -> its three columns; a scalar scale -> repeated; rgb -> rgba with a = fill)."""
    if x is None:
        return np.full((n, k), fill)
    a = np.asarray(x, float)
    if a.ndim == 0:
        return np.full((n, k), float(a))
    a = a.reshape(-1, a.shape[-1]) if a.ndim > 1 else a.reshape(1, -1)
    if a.shape[1] == 1 and k > 1:
        a = np.repeat(a, k, 1)
    if a.shape[1] < k:
        a = np.hstack([a, np.full((len(a), k - a.shape[1]), fill)])
    return np.broadcast_to(a[:, :k], (n, k)).copy()


def _verts(pos, nrm, uv4, pblock) -> np.ndarray:
    v = np.zeros((len(pos), FLOATS), np.float32)
    v[:, 0:3], v[:, 3:6], v[:, 6:10] = pos, nrm, uv4
    v[:, 10:] = pblock
    return v


# ---- meshes --------------------------------------------------------------------------------------------------------------

def quat_mat(q) -> np.ndarray:
    """(n, 4) quaternions (x, y, z, w) -> (n, 3, 3)."""
    q = np.asarray(q, float).reshape(-1, 4)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    x, y, z, w = q.T
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], 1)


def mesh_transforms(pos, scale=None, orient=None) -> np.ndarray:
    """(n, 4, 4) world matrices of mesh particles: T(pos) R(orientation quaternion) S(scale, float or float3)."""
    pos = np.asarray(pos, float).reshape(-1, 3)
    n = len(pos)
    R = quat_mat(orient) if orient is not None else np.tile(np.eye(3), (n, 1, 1))
    if len(R) == 1 and n > 1:
        R = np.repeat(R, n, 0)
    S = _rows(scale, n, 3, 1.0)
    M = np.tile(np.eye(4), (n, 1, 1))
    M[:, :3, :3] = R * S[:, None, :]
    M[:, :3, 3] = pos
    return M


def pack_mesh(mpos, mnrm, muv, tris, xforms, pblock, valpha=None, dmask=None) -> np.ndarray:
    """Every particle's instance of a mesh: (n x tris x 3, FLOATS) vertices in world space. `valpha` (per mesh vertex,
    0..1): VERTEX_ALPHA, the mesh's vertex colour alpha times the particle colour's alpha. The game displaces a mesh
    vertex in MESH space, `pos += normal * (map.r * 2 - 1) * vertex colour R * DisplacementDistance`, before the instance
    matrix (calib2: D2R's uber mesh VS permutation 1733, SPIR-V of the Fire Bolt head draw), so each vertex's distance
    (`sc.w`) is scaled here by the instance matrix's stretch of its normal and by `dmask` (vertex colour R, 0..1)."""
    idx = np.asarray(tris).reshape(-1)
    va = None if valpha is None else np.asarray(valpha, np.float32)[idx]
    dm = None if dmask is None else np.asarray(dmask, np.float32)[idx]
    P, N, UV = np.asarray(mpos, float)[idx], np.asarray(mnrm, float)[idx], np.asarray(muv, float)[idx]
    out = []
    for M, pb in zip(np.asarray(xforms, float), np.asarray(pblock, np.float32)):
        A = M[:3, :3]
        Ninv = np.linalg.inv(A).T if abs(np.linalg.det(A)) > 1e-12 else A
        Pw = P @ A.T + M[:3, 3]
        Nw = N @ Ninv.T
        Nw /= np.maximum(np.linalg.norm(Nw, axis=1, keepdims=True), 1e-12)
        v = _verts(Pw, Nw, np.hstack([UV, UV]), np.tile(pb, (len(idx), 1)))
        stretch = np.linalg.norm(N @ A.T, axis=1) / np.maximum(np.linalg.norm(N, axis=1), 1e-12)
        v[:, OFF["sc"][0] + 3] *= stretch * (1.0 if dm is None else dm)
        if va is not None:
            v[:, OFF["color"][0] + 3] *= va
        out.append(v)
    return np.concatenate(out) if out else np.zeros((0, FLOATS), np.float32)


# ---- billboards ----------------------------------------------------------------------------------------------------------
CORNERS = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]])   # quad corners (x right, y up)
CORNER_UV = np.array([[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]])
QUAD_TRIS = np.array([0, 1, 2, 0, 2, 3])
WORLD_UP = np.array([0.0, 1.0, 0.0])


def _unit(v, fallback):
    v = np.asarray(v, float)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    fb = np.broadcast_to(np.asarray(fallback, float), v.shape)
    return np.where(n > 1e-6, v / np.maximum(n, 1e-12), fb)


def billboard_corners(mode, pos, size, rot, basis, axis=None, axis2=None, axis_scale=1.0, aspect=1.0):
    """(n, 4, 3) world corners (order of CORNERS) and (n, 4, 3) normals of billboards.

    pos (n, 3); size (n, 2) half extents (or (n, 1)); rot (n,) radians; basis (r, u, w) of the camera; axis/axis2 (n, 3)
    for the axial and planar modes. A capsule is drawn as its axial quad (the two cap triangles are left out)."""
    r, up, w = (np.asarray(b, float) for b in basis)
    pos = np.asarray(pos, float).reshape(-1, 3)
    n = len(pos)
    size = np.asarray(size, float).reshape(n, -1)
    sx = size[:, 0]
    sy = size[:, 1] if size.shape[1] > 1 else sx
    rot = np.zeros(n) if rot is None else np.asarray(rot, float).reshape(n)
    c, s = np.cos(rot), np.sin(rot)
    N = np.tile(-w, (n, 1))                     # eye -> particle (orthographic)
    cx, cy = CORNERS[:, 0], CORNERS[:, 1]
    nrm = np.tile(w, (n, 4, 1))
    if mode in (0, 1):
        if mode == 0:
            a = float(aspect)
            if a <= 1.0:
                sx = sx * max(a, 0.0)
            else:
                sy = sy / a
            R, U = np.tile(r, (n, 1)), np.tile(up, (n, 1))
        else:                                   # viewpos: axes facing the particle from the eye, world up kept
            fwd = -N
            R = _unit(np.cross(WORLD_UP, fwd), (1.0, 0.0, 0.0))
            U = np.cross(fwd, R)
        qx = cx[None, :] * 2.0 * sx[:, None]
        qy = cy[None, :] * 2.0 * sy[:, None]
        X = qx * c[:, None] - qy * s[:, None]
        Y = qx * s[:, None] + qy * c[:, None]
        P = pos[:, None, :] + R[:, None, :] * X[..., None] + U[:, None, :] * Y[..., None]
        return P, nrm
    if mode in (2, 3, 4):
        A = (np.zeros((n, 3)) if axis is None else np.asarray(axis, float).reshape(n, 3)) * float(axis_scale)
        L = np.linalg.norm(A, axis=1)
        d = _unit(A, up)
        if mode == 3:                           # spheroidal: S = d x N, U = d L/2 + N x S
            S = _unit(np.cross(d, N), r) * sx[:, None]
            U = d * (0.5 * L)[:, None] + np.cross(N, S)
            P = pos[:, None, :] + U[:, None, :] * (cy * 2.0)[None, :, None] - S[:, None, :] * (cx * 2.0)[None, :, None]
        else:                                   # axial quad (and capsule body): P +- S +- axis/2, S = d x (eye - P)
            # (CAxialBillboarderQuad as PopcornFX-Web transcribes it: side = cross(dir, toEye); lane r3-cull: d x N had
            # the quad facing away, which the game's default back culling of modes 2-4 would hide)
            S = _unit(np.cross(N, d), r) * sx[:, None]
            P = pos[:, None, :] + S[:, None, :] * (cx * 2.0)[None, :, None] + d[:, None, :] * (cy * L[:, None])[..., None]
        return P, nrm
    if mode == 5:                               # planar: X = cross(axis2, axis), Y = cross(X, axis2) (axis2 not normalised)
        A = np.tile(up, (n, 1)) if axis is None else np.asarray(axis, float).reshape(n, 3)
        B = np.tile(up, (n, 1)) if axis2 is None else np.asarray(axis2, float).reshape(n, 3)
        X = _unit(np.cross(B, A), r)
        Y = np.cross(X, B)
        Xs, Ys = X * (2.0 * sx * float(axis_scale))[:, None], Y * (2.0 * sy)[:, None]   # size = half extent (game quads)
        P = pos[:, None, :] - (cx[None, :, None] * (c[:, None, None] * Xs[:, None] + s[:, None, None] * Ys[:, None])) \
            + cy[None, :, None] * (c[:, None, None] * Ys[:, None] - s[:, None, None] * Xs[:, None])
        nb = _unit(B, w)
        return P, np.repeat(nb[:, None, :], 4, 1)
    raise ValueError("billboard mode %r" % (mode,))


def atlas_rects(subdiv) -> np.ndarray:
    """Procedural atlas (Atlas.SubDiv cols x rows): (frames, 4) rects u0, v0, u1, v1, row-major from the top left."""
    cols, rows = int(subdiv[0]), int(subdiv[1]) if len(subdiv) > 1 else int(subdiv[0])
    cols, rows = max(cols, 1), max(rows, 1)
    j, i = np.divmod(np.arange(cols * rows), cols)
    return np.stack([i / cols, j / rows, (i + 1) / cols, (j + 1) / rows], 1).astype(np.float32)


def atlas_frame(tid, frames: int):
    """TextureID -> (frame, next frame, blend), clamped as CBillboarder::FillTexcoordsFromAtlas does."""
    t = np.abs(np.nan_to_num(np.asarray(tid, float)))
    t = np.minimum(t, frames - 1)
    fa = np.floor(t).astype(int)
    return fa, np.minimum(fa + 1, frames - 1), (t - fa).astype(np.float32)


# vertex misc.y is shared (integration r3): AlphaMask.AtlasAlphaOnly's atlas cell + MISC_Y_CELL_BIAS (cells -2048 ..
# 2047, the VS's sign rule gives negative ones) + MISC_Y_SPAN x the colour variant's quantised exponent
# (colorvariant.pack_misc_y). fx.wgsl vs_fx splits them; everything stays an exact integer in f32.
MISC_Y_SPAN = 4096.0
MISC_Y_CELL_BIAS = 2048.0


def atlas_cell(tid, subdiv) -> np.ndarray:
    """AlphaMask.AtlasAlphaOnly (lane r3-atlasalpha): the atlas cell index the game's atlas VS (vs_popcorn_uber_billboard_v
    #352/#359) picks from the raw TextureID, g = TexID mod (cols x rows) with the shader's sign rule (no clamp, unlike
    atlas_frame). The cell origin is `atlas_cell_origin(g, subdiv)`."""
    cols, rows = int(subdiv[0]) or 1, (int(subdiv[1]) if len(subdiv) > 1 else int(subdiv[0])) or 1
    n = float(cols * rows)
    t = np.nan_to_num(np.asarray(tid, np.float64))
    s = np.where(t * n >= 0, n, -n)
    return np.floor((t / s - np.floor(t / s)) * s).astype(np.float32)


def atlas_cell_origin(g, subdiv) -> np.ndarray:
    """(n, 2) uv origin of atlas cell `g`, as the game's atlas VS computes it: column = g mod cols (sign rule as
    atlas_cell), row = floor(g / rows) (the shader divides by the row count, not the column count: the same for the
    square atlases of every AtlasAlphaOnly renderer in the corpus). Mirrored by view/fx.wgsl `cell_origin`."""
    cols, rows = float(int(subdiv[0]) or 1), float((int(subdiv[1]) if len(subdiv) > 1 else int(subdiv[0])) or 1)
    g = np.asarray(g, np.float64)
    c = np.where(g * cols >= 0, cols, -cols)
    col = c * (g / c - np.floor(g / c))
    row = np.floor(g / rows)
    return np.stack([col / cols, row / rows], -1).astype(np.float32)


def atlas_local_uv(uv, origin, subdiv) -> np.ndarray:
    """Cell-local uv of the AtlasAlphaOnly textures: sat((uv - origin) x SubDiv) (VS #352)."""
    sd = np.asarray([float(subdiv[0]), float(subdiv[1] if len(subdiv) > 1 else subdiv[0])])
    return np.clip((np.asarray(uv, np.float64) - origin) * sd, 0.0, 1.0).astype(np.float32)


def sort_order(depth, kind: int):
    """Back-to-front order of particles for blend kinds that need it (alpha 2, premultiplied 3); None for additive and
    multiplicative (commutative). `depth`: camera.depth (larger = nearer)."""
    if kind in (0, 1, 4):
        return None
    return np.argsort(np.asarray(depth, float), kind="stable")


def pack_billboards(mode, pos, size, rot, basis, pblock, axis=None, axis2=None, axis_scale=1.0, aspect=1.0,
                    tid=None, rects=None, blend_frames=False, order=None, flip_v=False, bend=0.0,
                    alpha_only_subdiv=None) -> np.ndarray:
    """(n x 6, FLOATS) vertices of billboards; `rects` the atlas (frames, 4) picked by `tid`; `order` the particle order;
    `bend`: NormalBend.NormalBendingFactor (lit materials, bend_normals); `alpha_only_subdiv` (AlphaMask.AtlasAlphaOnly:
    the Atlas.SubDiv): misc.y carries the game's cell index `atlas_cell(tid)` (+ MISC_Y_CELL_BIAS), from which fx.wgsl derives the cell-local
    uv of every texture but the alpha mask."""
    P, Nm = billboard_corners(mode, pos, size, rot, basis, axis, axis2, axis_scale, aspect)
    if bend:
        Nm = bend_normals(P, Nm, pos, bend)
    n = len(P)
    uv = np.tile(CORNER_UV, (n, 1, 1)).astype(np.float32)
    if flip_v:
        uv[..., 1] = 1.0 - uv[..., 1]
    uv2 = uv.copy()
    pblock = np.array(pblock, np.float32, copy=True)
    if rects is not None and len(rects):
        tid = np.zeros(n) if tid is None else np.asarray(tid, float).reshape(n)
        fa, fb, bl = atlas_frame(tid, len(rects))
        ra, rb = rects[fa], rects[fb]
        uv = ra[:, None, :2] + uv * (ra[:, None, 2:] - ra[:, None, :2])
        uv2 = rb[:, None, :2] + uv2 * (rb[:, None, 2:] - rb[:, None, :2])
        if blend_frames:
            pblock[:, OFF["misc"][0] - 10] = bl
        if alpha_only_subdiv is not None:
            # added: misc.y may already hold the colour variant's exponent x 4096 (colorvariant.pack_misc_y)
            pblock[:, OFF["misc"][0] - 10 + 1] += atlas_cell(tid, alpha_only_subdiv) + MISC_Y_CELL_BIAS
    if order is not None:
        P, Nm, uv, uv2, pblock = P[order], Nm[order], uv[order], uv2[order], pblock[order]
    idx = QUAD_TRIS
    Pv = P[:, idx].reshape(-1, 3)
    Nv = Nm[:, idx].reshape(-1, 3)
    UV = np.concatenate([uv[:, idx], uv2[:, idx]], -1).reshape(-1, 4)
    return _verts(Pv, Nv, UV, np.repeat(pblock, 6, 0))


def bend_normals(P, Nm, pos, factor) -> np.ndarray:
    """NormalBend (lit billboards): corner normals tilted outwards from the particle centre, n = normalize(N (1 - f) +
    d f) with d the unit direction from the centre to the corner. GUESS: D2R bends on the CPU billboarder (the lit VS
    takes the normals as given, docs/hdfx-shaders.md 10); this is PopcornFX's usual meaning of the factor."""
    d = np.asarray(P, float) - np.asarray(pos, float).reshape(-1, 1, 3)
    d /= np.maximum(np.linalg.norm(d, axis=-1, keepdims=True), 1e-9)
    f = float(factor)
    n = np.asarray(Nm, float) * (1.0 - f) + d * f
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


# ---- ribbons -------------------------------------------------------------------------------------------------------------

def pack_ribbon(pos, width, basis, pblock, mode=0, axis=None, u=None, flip_u=False, flip_v=False,
                rotate=False) -> np.ndarray:
    """One ribbon strip through the points `pos` (k, 3) in order (newest first), half width `width` (k,) or scalar.
    Side vector per point (CRibbonBillboarder): view-aligned cross(T, P - eye) with T the central tangent; mode 1
    cross(axis, T), mode 2 the axis itself. U along the strip: `u` (k,) (CustomTextureU) or once per segment.
    `rotate` (TextureUVs.RotateTexture): see ribbon_uv."""
    pos = np.asarray(pos, float).reshape(-1, 3)
    k = len(pos)
    if k < 2:
        return np.zeros((0, FLOATS), np.float32)
    _r, _up, w = (np.asarray(b, float) for b in basis)
    T = np.empty_like(pos)
    T[1:-1] = pos[2:] - pos[:-2]
    T[0], T[-1] = pos[1] - pos[0], pos[-1] - pos[-2]
    if mode == 2 and axis is not None:
        side = _unit(axis, (1.0, 0.0, 0.0))
    elif mode == 1 and axis is not None:
        side = _unit(np.cross(axis, T), (1.0, 0.0, 0.0))
    else:
        side = _unit(np.cross(T, -w), (1.0, 0.0, 0.0))
    wd = np.broadcast_to(np.asarray(width, float).reshape(-1), (k,))
    Pp, Pm = pos + side * wd[:, None], pos - side * wd[:, None]
    pblock = np.asarray(pblock, np.float32).reshape(-1, PARTICLE_FLOATS)
    pblock = np.broadcast_to(pblock, (k, PARTICLE_FLOATS)) if len(pblock) in (1, k) else pblock[:k]
    verts = []
    for j in range(k - 1):
        if u is not None:
            ua, ub = float(u[j]), float(u[j + 1])
        else:
            ua, ub = 0.0, 1.0
        uv = lambda t, sd: ribbon_uv(t, sd, flip_u, flip_v, rotate)      # noqa: E731
        quad = [(Pp[j], uv(ua, 0.0), j), (Pm[j], uv(ua, 1.0), j), (Pp[j + 1], uv(ub, 0.0), j + 1),
                (Pm[j], uv(ua, 1.0), j), (Pm[j + 1], uv(ub, 1.0), j + 1), (Pp[j + 1], uv(ub, 0.0), j + 1)]
        for P, (uu, vv), q in quad:
            verts.append(_verts(P[None], w[None], np.array([[uu, vv, uu, vv]]), pblock[q][None]))
    return np.concatenate(verts)


def ribbon_uv(t, side, flip_u=False, flip_v=False, rotate=False):
    """A ribbon vertex's texture uv from its position along the strip `t` (0..1 per segment, or CustomTextureU, not
    normalised: material samplers wrap, so a TextureU past 1 repeats) and its side (0 on the + side, 1 on the - side).
    D2R packs TextureUVs into PopcornFX's billboarder UV flags (0x14100e130: RotateTexture 1, FlipU 2, FlipV 4,
    FlipUVs xor 6); the CPU ribboner then lays the corners as PopcornFX-Web's RIB_ROWS (MIT; read from the PopcornFX
    runtime's CRibbonBillboarder): plain u = t (1 - t with FlipU), v = side (1 - side with FlipV); rotated
    u = side (1 - side with FlipV), v = 1 - t (t with FlipU). TextureRepeat is not read by D2R (its property global
    is only reset, 0x141728c40): repeating comes from the unnormalised TextureU and the wrap samplers alone."""
    if rotate:
        return (1.0 - side if flip_v else side, t if flip_u else 1.0 - t)
    return (1.0 - t if flip_u else t, 1.0 - side if flip_v else side)


# ---- decals and lights ---------------------------------------------------------------------------------------------------

def decal_box(pos, size, orient=None) -> np.ndarray:
    """4x4 world -> decal space ([-0.5, 0.5]^3) of a box at `pos` with full extents `size` (3,), `orient` a quaternion."""
    R = quat_mat(orient)[0] if orient is not None else np.eye(3)
    M = np.eye(4)
    M[:3, :3] = R * np.asarray(size, float)[None, :]
    M[:3, 3] = pos
    return np.linalg.inv(M)


def decal_quad(world_to_box, basis, pblock) -> np.ndarray:
    """A camera-facing quad (6 vertices) covering the screen bounds of the decal box, at its nearest depth."""
    r, up, w = (np.asarray(b, float) for b in basis)
    B = np.linalg.inv(world_to_box)
    cube = np.array([[x, y, z, 1.0] for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)])
    C = (cube @ B.T)[:, :3]
    xr, yr, zr = C @ r, C @ up, C @ w
    x0, x1, y0, y1, zn = xr.min(), xr.max(), yr.min(), yr.max(), zr.max() + 0.01
    pts = np.array([r * x + up * y + w * zn for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
    idx = QUAD_TRIS
    uv = np.tile(CORNER_UV, (1, 2))[idx]
    return _verts(pts[idx], np.tile(w, (6, 1)), uv, np.tile(np.asarray(pblock, np.float32).reshape(1, -1), (6, 1)))


# D2R's point light (calib3: the terrain forward shader's light loop, g4 capture 4750 draw 15460, light records in
# its typed buffer t0): record = position, colour x power / (4 pi), range R, exponent e; diffuse =
# albedo / pi x N.L x colour x max(1 - (D / R)^e, 0)^2 / (1 + LIGHT_INV_SQ x D^2), D the 3D distance. Fire Bolt's light
# (Power 5000, AttenuationSteepness 0.2, Range 20) holds 397.9 x Light.Color, R 20, e 1.6: e = 8 x steepness
# (LIGHT_STEEP_TO_EXP, one sample). The falloff and this colour reproduce its ground pool in the game shots (calib3).
LIGHT_INV_SQ = 0.092903040349483489990234375
LIGHT_STEEP_TO_EXP = 8.0


@dataclass
class FxLight:
    pos: tuple
    color: tuple          # rgb radiance as the game's record holds it: Light.Color x power / (4 pi)
    radius: float
    atten: float = 1.0    # the game's falloff exponent e (8 x AttenuationSteepness)

    def point(self) -> dict:
        """As a gpu.Light point light in the game's form: colour / pi (the Lambert 1/pi folded in), and a NEGATIVE
        attenuation -e, which the WGSL point loops read as max(1 - (d / radius)^e, 0)^2 / (1 + LIGHT_INV_SQ d^2) (a
        positive one keeps the viewer's map-light form (1 - (d / radius)^2)^atten)."""
        return {"pos": [float(x) for x in self.pos], "radius": float(self.radius),
                "color": [float(x) / math.pi for x in self.color], "atten": -max(float(self.atten), 1e-3), "fx": True}


def light_falloff(d, radius, atten):
    """The point loops' distance term (atten < 0: the game's form with exponent -atten, else the map-light form)."""
    d = np.asarray(d, float)
    if atten < 0:
        w = np.maximum(1.0 - np.power(np.minimum(d / max(radius, 1e-6), 1.0), -atten), 0.0)
        return w * w / (1.0 + LIGHT_INV_SQ * d * d)
    return np.power(np.clip(1.0 - d * d / max(radius * radius, 1e-6), 0.0, 1.0), atten)


def lights_from(pos, color, radius, power=1.0, steepness=1.0) -> list:
    """FxLight per particle: Light.Color x Light.Power / (4 pi), the renderer's LightAttenuation.AttenuationSteepness
    x 8 as the game's falloff exponent (see LIGHT_INV_SQ)."""
    pos = np.asarray(pos, float).reshape(-1, 3)
    n = len(pos)
    col = _rows(color, n, 4, 1.0)
    rad = np.broadcast_to(np.asarray(radius, float).reshape(-1), (n,))
    pw = np.broadcast_to(np.asarray(power, float).reshape(-1), (n,))
    out = []
    for i in range(n):
        c = col[i, :3] * col[i, 3] * pw[i] / (4.0 * math.pi)
        out.append(FxLight(tuple(pos[i]), tuple(c), float(rad[i]), LIGHT_STEEP_TO_EXP * float(steepness)))
    return out
