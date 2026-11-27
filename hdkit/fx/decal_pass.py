"""All FX decals of a frame in one draw pair, as D2R's terrain shader takes them (lane hdfx-r3-decal2).

The game (docs/hdfx-shaders.md 13; ps_terrain's decal loop): every decal of the clustered list is mixed into the
surface MATERIAL in list order and the walk stops once the summed weights reach 1; the lighting then runs once. Only the
terrain and model draws flagged with cb2[20].x bit 16 take decals; units are not relit.

The viewer: `group(draws)` replaces the per-decal draws (render.FxRenderer._prep_decal, one record each) by two
`DecalGroupDraw`s (`fs_decals_mul`, `fs_decals_add` in view/fx_decals.wgsl) that walk every record in game order
(`Record.seq`: allocation order, effect by effect), with the records in a storage buffer and the maps in texture arrays
(bind group 1, as the game's t30-t32 arrays). Receivers: the ground plane (render `floor`, else y 0) within
`RECEIVER_TOL` world units; `RECEIVERS = False` relights every surface in the box (the r3-decal behaviour).
"""
from __future__ import annotations

import numpy as np

from . import decal as dc
from .render import SHADER, FxDraw

RECEIVERS = True
RECEIVER_TOL = 0.25            # world units: a pixel on the ground plane (the depth is resolved from 32-bit float)
SLICE_MAX = 1024               # slice side of the texture arrays (the game's slices are the textures' own sizes)
DEFAULTS = {"alb": (255, 255, 255, 255), "nrm": (128, 128, 255, 128), "orm": (255, 255, 0, 255), "em": (0, 0, 0, 255)}
MAPS = (("alb", 0, True), ("nrm", 10, False), ("orm", 1, False), ("em", 2, False))   # (array, material slot, sRGB)
ROWS = 8


def records_buffer(recs, slices, plane_y=0.0, tol=RECEIVER_TOL, receivers=True) -> np.ndarray:
    """The storage buffer of view/fx_decals.wgsl: a header (n, plane y, tolerance, receivers on), then 8 vec4 per record.
    `slices[i]` = (albedo, normal, ORM, emissive) slice of record i."""
    out = np.zeros((1 + ROWS * len(recs), 4), np.float32)
    out[0] = (len(recs), plane_y, tol, float(bool(receivers)))
    for i, (r, s) in enumerate(zip(recs, slices)):
        b = 1 + ROWS * i
        out[b:b + 3] = r.world_to_box[:3]
        out[b + 3] = (*r.x_axis, r.flags)
        out[b + 4] = (*r.y_axis, r.edge_f())
        cmin, cmax = r.cos_range()
        out[b + 5] = (cmin, cmax, r.thr_f(), r.sharp())
        out[b + 6] = (r.em_intensity(), r.em_power(), s[0], s[1])
        out[b + 7] = (s[2], s[3], 0.0, 0.0)
    return out


def order(draws) -> list:
    """The decal draws' records in the game's list order (Record.seq; ties keep the given order), one per decal."""
    seen, out = set(), []
    for d in draws:
        r = d.decal_rec
        if r is None or id(r) in seen:
            continue
        seen.add(id(r))
        out.append(d)
    return sorted(out, key=lambda d: getattr(d.decal_rec, "seq", (0, 0)))


def _resize_mips(mips, side):
    """A mip chain of exactly `side` x `side` (PIL resample of the largest level, then halvings)."""
    from PIL import Image
    top = np.asarray(mips[0], np.uint8)[..., :4]
    if top.shape[-1] < 4:
        top = np.dstack([top, np.full(top.shape[:2], 255, np.uint8)])
    im = Image.fromarray(np.ascontiguousarray(top), "RGBA")
    out = []
    s = side
    while s >= 1:
        out.append(np.asarray(im.resize((s, s), Image.BILINEAR), np.uint8))
        s //= 2
    return out


class DecalSet:
    """GPU resources of one ordered list of decals: texture arrays (one slice per distinct map) and the record buffer."""

    def __init__(self, fxr, draws):
        self.fxr = fxr
        self.draws = draws
        self.recs = [d.decal_rec for d in draws]
        self.paths = {k: [] for k, _s, _c in MAPS}
        self.slices = []
        for d in draws:
            row = []
            for k, slot, _srgb in MAPS:
                p = d.material.textures.get(slot) or ""
                lst = self.paths[k]
                if p not in lst:
                    lst.append(p)
                row.append(lst.index(p))
            self.slices.append(row)
        self._bind = {}

    def _mips(self, path, srgb, kind):
        from ..view import scene
        f = self.fxr
        if path:
            try:
                if f.vfs.exists(path):
                    return scene.read_texture(f.vfs, path, srgb=srgb).mips
                f.notes.append("decal texture %s: missing; default used" % path)
            except (KeyError, ValueError) as e:
                f.notes.append("decal texture %s: %s; default used" % (path, e.__class__.__name__))
        return [np.full((4, 4, 4), DEFAULTS[kind], np.uint8)]

    def arrays(self):
        """{kind: 2d-array view}, cached on the FxRenderer by the list of paths."""
        f = self.fxr
        cache = f.__dict__.setdefault("_decal_arrays", {})
        out = {}
        for k, _slot, srgb in MAPS:
            key = (k, tuple(self.paths[k]))
            if key not in cache:
                chains = [self._mips(p, srgb, k) for p in self.paths[k]]
                side = min(SLICE_MAX, max(max(c[0].shape[:2]) for c in chains))
                side = 1 << int(np.ceil(np.log2(max(side, 4))))
                if side > SLICE_MAX:
                    side = SLICE_MAX
                cache[key] = f.gpu.texture_array([_resize_mips(c, side) for c in chains], srgb=srgb)
            out[k] = cache[key]
        return out

    def bind_group(self, plane_y, tol=None, receivers=None):
        tol = RECEIVER_TOL if tol is None else tol
        receivers = RECEIVERS if receivers is None else receivers
        f = self.fxr
        g = f.gpu
        key = (round(float(plane_y), 5), tol, receivers)
        if key not in self._bind:
            data = records_buffer(self.recs, self.slices, plane_y, tol, receivers)
            buf = g.device.create_buffer_with_data(data=data.tobytes(), usage=g.wgpu.BufferUsage.STORAGE)
            a = self.arrays()
            self._bind[key] = g.device.create_bind_group(layout=layout(f), entries=[
                {"binding": 0, "resource": {"buffer": buf, "offset": 0, "size": data.nbytes}},
                {"binding": 1, "resource": a["alb"]}, {"binding": 2, "resource": a["nrm"]},
                {"binding": 3, "resource": a["orm"]}, {"binding": 4, "resource": a["em"]}])
        return self._bind[key]

    def points(self) -> np.ndarray:
        """Corners of every decal box (world), for the renderer's depth range."""
        c = np.array([[x, y, z, 1.0] for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)])
        pts = [(np.linalg.inv(r.world_to_box) @ c.T).T[:, :3] for r in self.recs]
        return np.concatenate(pts) if pts else np.zeros((0, 3))


def layout(fxr):
    """Bind group 1 layout of the decal pass (records + 4 texture arrays), made once per FxRenderer."""
    if "_decal_bgl" not in fxr.__dict__:
        g = fxr.gpu
        T = g.wgpu.ShaderStage
        arr = {"sample_type": "float", "view_dimension": "2d-array"}
        fxr._decal_bgl = g.device.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.FRAGMENT, "buffer": {"type": "read-only-storage"}},
            *[{"binding": b, "visibility": T.FRAGMENT, "texture": arr} for b in (1, 2, 3, 4)]])
    return fxr._decal_bgl


def pipeline(fxr, entry):
    """The pipeline of `fs_decals_mul` (dst x src) or `fs_decals_add` (one, one): a full-screen triangle, no depth test."""
    cache = fxr.__dict__.setdefault("_decal_pipes", {})
    if entry in cache:
        return cache[entry]
    g = fxr.gpu
    if "_decal_module" not in fxr.__dict__:
        src = SHADER.read_text(encoding="utf-8") + "\n" + (SHADER.parent / "fx_decals.wgsl").read_text(encoding="utf-8")
        fxr._decal_module = g.device.create_shader_module(code=src)
    keep = {"src_factor": "zero", "dst_factor": "one", "operation": "add"}
    blend = {"color": {"src_factor": "dst", "dst_factor": "zero", "operation": "add"}, "alpha": keep} \
        if entry == "fs_decals_mul" else {"color": {"src_factor": "one", "dst_factor": "one", "operation": "add"},
                                          "alpha": keep}
    p = g.device.create_render_pipeline(
        layout=g.device.create_pipeline_layout(bind_group_layouts=[fxr.bgl, layout(fxr)]),
        vertex={"module": fxr._decal_module, "entry_point": "vs_decals", "buffers": []},
        primitive={"topology": "triangle-list", "cull_mode": "none"},
        depth_stencil={"format": "depth32float", "depth_write_enabled": False, "depth_compare": "always"},
        multisample={"count": g.msaa},
        fragment={"module": fxr._decal_module, "entry_point": entry,
                  "targets": [{"format": "rgba16float", "blend": blend}]})
    cache[entry] = p
    return p


class DecalGroupDraw(FxDraw):
    """One of the two full-screen draws of a DecalSet: its bounds are the decal boxes, it goes before every other draw."""
    decal_set: DecalSet | None = None

    def points(self):
        return self.decal_set.points()

    def sort_key(self, cam):
        return -1e30


def group(fxr, draws) -> list:
    """`draws` with its per-decal draws replaced by the two full-screen decal draws (first: the game mixes decals into
    the surface before anything is drawn over it). Unchanged when there is no decal."""
    dec = order(draws)
    if not dec:
        return list(draws)
    ds = DecalSet(fxr, dec)
    first = dec[0]
    out = []
    for entry in ("fs_decals_mul", "fs_decals_add"):
        d = DecalGroupDraw("decal", first.renderer, first.layer, first.material, np.zeros((3, first.verts.shape[1]),
                           np.float32), entry=entry, decal=first.decal, decal_rec=first.decal_rec,
                           name="decals:%d" % len(dec))
        d.decal_set = ds
        out.append(d)
    return out + [d for d in draws if d.decal_rec is None]


# ---- CPU reference of view/fx_decals.wgsl (tests) ----------------------------------------------------------------------

def blend(layers, ground_normal=(0.0, 1.0, 0.0), ground_rough=dc.GROUND_ROUGHNESS, v=None) -> dict:
    """The material after the game's walk over `layers` [(Record, w, tex)] (tex as decal.shade takes it): normal,
    occlusion, roughness, metalness mixed in order, albedo split as A x cA + aD (A the unknown ground albedo), emissive
    (decals' albedo part), the summed weight, and how many decals were taken before the walk stopped."""
    n = np.asarray(ground_normal, float)
    ao, rough, metal, cA = 1.0, float(ground_rough), 0.0, 1.0
    aD, em, sw, used = np.zeros(3), np.zeros(3), 0.0, 0
    for rec, w, tex in layers:
        f = rec.flags
        used += 1
        if f & dc.OCCLUSION:
            ao += w * (tex["orm"][0] - ao)
        if f & dc.ROUGHNESS:
            rough += w * (tex["orm"][1] - rough)
        if f & dc.METALNESS:
            metal += w * (tex["orm"][2] - metal)
        if f & dc.NORMAL:
            nm = tex["nrm"]
            x, y = nm[3] * 2.0 - 1.0, nm[1] * 2.0 - 1.0
            z = np.sqrt(1.0 - min(x * x + y * y, 1.0))
            d = x * rec.x_axis + y * np.cross(rec.x_axis, rec.y_axis) + z * rec.y_axis
            d = d / np.linalg.norm(d)
            n = n + w * (d - n)
            n = n / np.linalg.norm(n)
        if f & dc.ALBEDO:
            aD = aD + w * (np.asarray(tex["albedo"], float) - aD)
            cA *= 1.0 - w
        if f & dc.EMISSIVE and v is not None:
            nv = float(np.dot(n, v))
            em = em + aD * w * rec.em_intensity() * tex["em"] * max(nv, 1e-3) ** rec.em_power() * (nv + 1.0) / np.pi
        sw += w
        if sw >= 1.0:
            break
    return {"normal": n, "ao": ao, "rough": max(rough, 0.04), "metal": metal, "cA": cA, "aD": aD, "emissive": em,
            "sum": sw, "used": used}
