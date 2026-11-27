"""D2R's FX decals (Diablo_Decal, renderer kind 5) as the game draws them (lane hdfx-r3-decal, docs/hdfx-shaders.md 13).

Where the game does it (build 95032, D2R.exe statics, RVAs):
- **Shaders**: no decal shader exists. `ps_terrain` (and `ps_models_p` for draws whose cb2[20].x has bit 16) walk a
  clustered list (t4 tile counts, t5 tile indices, t8 float4 records of 7 texels per decal), test the pixel's
  view-space position against the decal box and blend the surface MATERIAL before lighting:
  albedo, normal, occlusion, roughness, metalness are each lerped towards the decal's value by the decal weight
  (in list order; the walk stops once the summed weights reach 1), then the normal lighting runs on the result, so a
  decal lights like the ground (wet blood: roughness 0.04, glossy). An emissive term is added before the exposure.
  Textures are single-channel / 3-channel slices of texture arrays: t30 albedo `$RGB`, t31 normal `$NRM` (DXT5nm, x
  in alpha, y in green), t32 one channel: albedo `$A` (alpha), ORM `$R/$G/$B/$A` (occlusion, roughness, metalness,
  threshold mask) and emissive `$R` (`DecalManager` slice builder 0xe6d780; suffix strings 0x1ddf30c ...).
- **Record packer** 0x101cbf0 (PopcornLightPolicy streams gathered by 0x101ba90): record = rows 0-2 view -> box
  ([-0.5, 0.5]^3) of  S(Decal.DecalSize, full extents) R(Decal.Direction) T(Position) x view, row 3 packed bytes,
  rows 4/5 the box X and Y axes in view space (Y = -Direction: the projection axis and the normal map's z), row 6 the
  eight slice ids. Row 3 bytes (`pack`): x: EdgeFade x 255, cos(FadeOutAngle) x 127, cos(FadeInAngle) x 127 (angle
  test only when the two angles differ); y: ThresholdValue x 255, ThresholdHardness x 255 (byte 2, the box-height fade,
  is never written: 0); w: emissive (log10(Emissive.Emissive.Intensity) - 2) / 4 x 255, Emissive.Emissive.Focus / 8 x
  255, flags (bits 16..): 1 angle, 2 albedo, 4 normal, 8 occlusion, 16 roughness, 32 metalness (each `ApplyTo*` and its
  slice present), 64 threshold (ThresholdValue > 0 and an ORM map), 128 emissive (`Emissive` and an EmissiveMap).
- **Material reader** 0x101c4e0 (vtable 0x1e2ca20 slot 6) reads only the four maps, the five `ApplyTo*` switches and
  `Emissive`. `Decal.OpaqueColor`, `Decal.OpaqueNormal`, `Decal.ThresholdAlphaSelect` and the `Decal.DecalWeight`
  input are read by neither the reader nor the packer: the game ignores them (UNUSED_BY_GAME).
- Kind 4 (`Default_Decal.pkma`, `GeometryDecal`): the renderer class picker 0xec3b60 builds classes for kinds 0-2 and 5
  only, so the game draws nothing for it (one material-editor preset uses it).

Per pixel (`shade`, the WGSL `fs_decal_*` do the same): p = the box position, uv = (p.x + 0.5, p.z + 0.5),
w = alpha x xz edge fade [x angle cut] [x saturate((orm.a - thr) x hard^2 x 64 + 0.5)], then the lerps above and
E += albedo' x w x 10^(2 + 4 e) x emissive.r x max(N'.V, 0.001)^(64 f) x (N'.V + 1) / pi.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import emissive

ANGLE, ALBEDO, NORMAL, OCCLUSION, ROUGHNESS, METALNESS, THRESHOLD, EMISSIVE = 1, 2, 4, 8, 16, 32, 64, 128
APPLY = (("Decal.ApplyToAlbedo", ALBEDO, "Decal.DiffuseMap"), ("Decal.ApplyToNormal", NORMAL, "Decal.NormalMap"),
         ("Decal.ApplyToOcclusion", OCCLUSION, "Decal.OrmMap"), ("Decal.ApplyToRoughness", ROUGHNESS, "Decal.OrmMap"),
         ("Decal.ApplyToMetalness", METALNESS, "Decal.OrmMap"))
UNUSED_BY_GAME = ("Decal.OpaqueColor", "Decal.OpaqueNormal", "Decal.ThresholdAlphaSelect", "Decal.DecalWeight")
# texture slots of a decal draw (render.FxRenderer binds slot -> t<slot>)
TEX_SLOTS = {0: "Decal.DiffuseMap", 1: "Decal.OrmMap", 2: "Decal.EmissiveMap", 10: "Decal.NormalMap"}
GROUND_ROUGHNESS = 0.9        # the surface under a decal when the viewer cannot know it (relighting ratio only)


def _map(props, name):
    v = props.get(name)
    return v if isinstance(v, str) and v and v.lower() != "none" else None


def material_flags(props) -> int:
    """The record's material bits (reader 0x101c4e0 + packer): an ApplyTo* switch counts only when its map exists;
    emissive needs the `Emissive` switch and an EmissiveMap."""
    f = 0
    for name, bit, mp in APPLY:
        if props.get(name) and _map(props, mp):
            f |= bit
    if props.get("Emissive") and _map(props, "Decal.EmissiveMap"):
        f |= EMISSIVE
    return f


def textures(props) -> dict:
    """slot -> logical texture path of a decal renderer."""
    return {s: _map(props, n) for s, n in TEX_SLOTS.items() if _map(props, n)}


def _byte(x) -> int:
    """(char)(int)(clamp(x, 0, 1) x 255), as the packer writes it (negative -> 0)."""
    return int(min(max(float(x), 0.0), 1.0) * 255.0)


def _sbyte(x) -> int:
    return int(min(max(float(x), -1.0), 1.0) * 127.0)


def box_axes(direction):
    """(X, Y, Z) of the decal box from Decal.Direction (0x101e2d0): Y = -normalize(direction); X = normalize(cross(Y,
    (Y.x, 0, Y.z))), or (1, 0, 0) when Y is vertical; Z = cross(X, Y)."""
    d = np.asarray(direction, float)
    n = float(np.linalg.norm(d))
    y = -(d / n) if n > 0 else np.array([0.0, 1.0, 0.0])
    a, b, c = y
    if math.sqrt(a * a + c * c) >= 1e-5:
        x = np.array([b * c, 0.0, -a * b])
        x /= np.linalg.norm(x)
    else:
        x = np.array([1.0, 0.0, 0.0])
    z = np.cross(x, y)
    return x, y, z / np.linalg.norm(z)


@dataclass
class Record:
    """One decal as the game's record holds it, in world space (the game's rows are the same in view space)."""
    world_to_box: np.ndarray      # 4x4
    x_axis: np.ndarray            # box X (tangent), world
    y_axis: np.ndarray            # box Y (projection axis, the normal map's z), world
    flags: int
    edge: int                     # bytes as packed
    cos_min: int
    cos_max: int
    thr: int
    hard: int
    em: int
    focus: int
    seq: tuple = (0, 0)           # the game's list order (decal_pass.order): (effect, particle)

    # decoded as the shader decodes them
    def edge_f(self) -> float:
        return self.edge / 255.0

    def cos_range(self):
        return max(self.cos_min / 127.0, -1.0), max(self.cos_max / 127.0, -1.0)

    def thr_f(self) -> float:
        return self.thr / 255.0

    def sharp(self) -> float:
        return (self.hard / 255.0) ** 2 * 64.0

    def em_intensity(self) -> float:
        return float(emissive.decode(self.em, self.focus)[0])

    def em_power(self) -> float:
        return float(emissive.decode(self.em, self.focus)[1])

    def uniforms(self) -> np.ndarray:
        """24 floats for view/fx.wgsl (FxU.f0, f1, f2, ops, inv, fres re-used by decal draws, see fs_decal_add)."""
        cmin, cmax = self.cos_range()
        return np.array([*self.x_axis, float(self.flags), *self.y_axis, self.edge_f(), cmin, cmax, self.thr_f(),
                         self.sharp(), self.em_intensity(), self.em_power(), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                         0.0, 0.0, 0.0, 0.0], np.float32)


def pack(pos, size, direction=(0.0, -1.0, 0.0), edge_fade=0.0, fade_in=0.0, fade_out=0.0, thr_value=0.0,
         thr_hardness=0.0, em_intensity=0.0, em_focus=0.0, mat_flags=0, has_orm=False) -> Record:
    """The record the packer 0x101cbf0 builds for one particle (angles in degrees)."""
    x, y, z = box_axes(direction)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2] = x * size[0], y * size[1], z * size[2]
    M[:3, 3] = pos
    flags = mat_flags & (ALBEDO | NORMAL | OCCLUSION | ROUGHNESS | METALNESS)
    cmin = cmax = 0
    if fade_in != fade_out:
        cmin, cmax = _sbyte(math.cos(math.radians(fade_out))), _sbyte(math.cos(math.radians(fade_in)))
        flags |= ANGLE
    if thr_value > 0.0 and has_orm:
        flags |= THRESHOLD
    thr, hard = _byte(thr_value), _byte(thr_hardness)
    em = focus = 0
    if mat_flags & EMISSIVE and em_intensity > 0.0:
        em, focus = (int(b) for b in emissive.encode(em_intensity, em_focus))   # the game's bytes (fx/emissive.py)
        flags |= EMISSIVE
    return Record(np.linalg.inv(M), x, y, flags, _byte(edge_fade), cmin, cmax, thr, hard, em, focus)


def _col(inputs, names, n, k, default):
    for nm in names:
        v = inputs.get(nm)
        if v is not None:
            a = np.asarray(v, float).reshape(len(v) if np.ndim(v) else 1, -1)
            if a.shape[0] == 1 and n > 1:
                a = np.repeat(a, n, 0)
            if a.shape[1] < k:
                a = np.concatenate([a, np.repeat(a[:, -1:], k - a.shape[1], 1)], 1)
            return a[:n, :k]
    return np.tile(np.asarray(default, float).reshape(1, k), (n, 1))


def records(props, inputs, n, pos) -> list[Record]:
    """Records of a renderer's n particles from its renderer inputs (render.inputs_for names)."""
    flags = material_flags(props)
    has_orm = _map(props, "Decal.OrmMap") is not None
    size = _col(inputs, ("Decal.DecalSize", "Scale", "Size"), n, 3, (1.0, 1.0, 1.0))
    dirs = _col(inputs, ("Decal.Direction",), n, 3, (0.0, -1.0, 0.0))
    get = lambda nm, d: _col(inputs, (nm,), n, 1, (d,))[:, 0]               # noqa: E731
    edge, fin, fout = get("Decal.EdgeFade", 0.0), get("Decal.FadeInAngle", 0.0), get("Decal.FadeOutAngle", 0.0)
    tv, th = get("Decal.ThresholdValue", 0.0), get("Decal.ThresholdHardness", 0.0)
    ei, ef = get("Emissive.Emissive.Intensity", 0.0), get("Emissive.Emissive.Focus", 0.0)
    out = []
    for i in range(n):
        if not np.all(np.isfinite(pos[i])) or not np.all(np.isfinite(size[i])) or np.any(np.abs(size[i]) < 1e-6):
            # a flat box (size 0 on an axis: a pool's first frame) covers nothing; the inverse would be singular
            out.append(None)
            continue
        out.append(pack(pos[i], size[i], dirs[i], edge[i], fin[i], fout[i], tv[i], th[i], ei[i], ef[i], flags, has_orm))
    return out


# ---- CPU reference of the shader (tests; the WGSL fs_decal_* follow it line by line) -------------------------------------

def coverage(rec: Record, p, alpha, orm_a=1.0, n_dot_axis=1.0):
    """The decal weight at box position p (3,) with the sampled albedo alpha and ORM alpha; 0 outside the box."""
    p = np.asarray(p, float)
    if np.any(np.abs(p) > 0.5):
        return 0.0
    w = float(alpha)
    e = rec.edge_f()
    if rec.edge > 0:
        h = 0.5 * e
        fx = 1.0 - min(max((abs(p[0]) - 0.5 * (1.0 - e)) / h, 0.0), 1.0)
        fz = 1.0 - min(max((abs(p[2]) - 0.5 * (1.0 - e)) / h, 0.0), 1.0)
        w *= fx * fz
    if rec.flags & ANGLE:
        lo, hi = rec.cos_range()
        w *= float(lo < n_dot_axis < hi)
    if rec.flags & THRESHOLD:
        w *= min(max((orm_a - rec.thr_f()) * rec.sharp() + 0.5, 0.0), 1.0)
    return w


def shade(rec: Record, w, base: dict, tex: dict, v=None) -> dict:
    """The surface material after one decal of weight w: base {albedo, normal, ao, rough, metal, emissive}; tex the
    samples {albedo (rgb), nrm (rgba DXT5nm), orm (rgba), em (r)}; v the unit vector towards the eye (emissive)."""
    out = {k: (np.array(v_, float) if isinstance(v_, (list, tuple, np.ndarray)) else v_) for k, v_ in base.items()}
    f = rec.flags
    n = np.asarray(base["normal"], float)
    if f & NORMAL:
        nm = tex["nrm"]
        x, yy = nm[3] * 2.0 - 1.0, nm[1] * 2.0 - 1.0
        z = math.sqrt(1.0 - min(x * x + yy * yy, 1.0))
        bt = np.cross(rec.x_axis, rec.y_axis)
        d = x * rec.x_axis + yy * bt + z * rec.y_axis
        d /= np.linalg.norm(d)
        n = n + w * (d - n)
        n = n / np.linalg.norm(n)
    out["normal"] = n
    if f & ALBEDO:
        out["albedo"] = np.asarray(base["albedo"], float) + w * (np.asarray(tex["albedo"], float) - base["albedo"])
    if f & OCCLUSION:
        out["ao"] = base["ao"] + w * (tex["orm"][0] - base["ao"])
    if f & ROUGHNESS:
        out["rough"] = base["rough"] + w * (tex["orm"][1] - base["rough"])
    if f & METALNESS:
        out["metal"] = base["metal"] + w * (tex["orm"][2] - base["metal"])
    if f & EMISSIVE and v is not None:
        nv = float(np.dot(n, v))
        k = rec.em_intensity() * tex["em"] * max(nv, 1e-3) ** rec.em_power() * (nv + 1.0) / math.pi
        out["emissive"] = np.asarray(base.get("emissive", 0.0), float) + np.asarray(out["albedo"], float) * w * k
    return out


# ---- coverage (render.SUPPORTED_* take these in) ----------------------------------------------------------------------
# The decal surface channels are drawn as above; the switches the game never reads are drawn as the game draws them
# (ignored); kind 4 / Default_Decal / GeometryDecal: the game builds no renderer for it, nor does the viewer.
SUPPORTED_FEATURES = {"Decal.ApplyToNormal", "Decal.ApplyToRoughness", "Decal.ApplyToOcclusion", "Decal.ApplyToMetalness",
                      "Decal.ThresholdAlphaSelect", "Decal.OpaqueColor", "Decal.OpaqueNormal", "GeometryDecal"}
# Decal `Emissive` (flag 128; drawn by fx/decal_pass fs_decals_add). The coverage name `Emissive` is only used by decal
# renderers in the corpus (20 effects), so it counts as supported here; lane r3-animuv adds the same name with the same
# byte encoding (its fx/emissive.encode/decode = pack()'s em/focus bytes), coordinated in docs/hdfx.md (lane r3-decal2).
SUPPORTED_FEATURES |= {"Emissive"}
SUPPORTED_KINDS = {"4"}
SUPPORTED_MATERIALS = {"vfx/Library/PopcornFXCore/Materials/Default_Decal.pkma"}


# ---- the game shot under a render (render.FxRenderer.render with bg) ------------------------------------------------------

def plate_view(gpu, scene_rgb):
    """A float (rgba16float) texture view of the shot's scene light (H, W, 3), for fs_decal_add: over a shot the effect
    pass starts from black and the shot is added afterwards (render.hdr_over_bg), so decals write the change
    plate x (k - 1) + add instead of relighting the target."""
    wgpu = gpu.wgpu
    a = np.ascontiguousarray(np.concatenate([np.asarray(scene_rgb, np.float32),
                                             np.ones(np.shape(scene_rgb)[:2] + (1,), np.float32)], -1), np.float16)
    h, w = a.shape[:2]
    t = gpu.device.create_texture(size=(w, h, 1), format="rgba16float",
                                  usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST)
    gpu.device.queue.write_texture({"texture": t, "mip_level": 0, "origin": (0, 0, 0)}, a.tobytes(),
                                   {"offset": 0, "bytes_per_row": w * 8, "rows_per_image": h}, (w, h, 1))
    return t.create_view()
