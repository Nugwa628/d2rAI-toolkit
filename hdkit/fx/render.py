"""Draw D2R's PopcornFX effects in the hdkit viewer (docs/hdfx.md section 3).

    fxr = FxRenderer(gpu, vfs)                            # view.gpu.Renderer, view.vfs.Layered
    draws = fxr.prepare(effect, state, cam)               # rspec.RenderEffect or graph.Effect, fx.sim SimState -> FxDraws
    img = fxr.render(draws, cam, size, base=prepared, light=Light(), post=Post(rig))

Transparent draws go after gpu.Renderer's opaque pass (units, maps) into the same MSAA target (FxOverlay, the
`overlay` hook of gpu.Renderer.render_rect): the scene depth is resolved to an r32float texture first (soft particles,
decals), then the effect draws are depth-tested against it, back to front by draw (camera depth of the draw's centre +
Transparent.CameraSortOffset), particles inside an alpha-blended draw back to front (packers.sort_order). Blend kinds:
PopcornFX Transparent.Type / Transparency.Type 0 Additive, 1 AdditiveNoAlpha, 2 AlphaBlend, 3 PremultipliedAlpha; the
`Multiplicative` switch 4; Opaque.Type 0 Solid 5, 1 Masked 6 (opaque effect draws write depth and go first).
Lights (Diablo_Light renderers) become point lights of the scene (gpu.Light.points): they light the units and the
ground (diffuse, as the game's map lights). Decals change the surface under them as the game's terrain
shader does (fx/decal.py).

Post: the light rig (view.rig.Rig) is the game's chain: exposure, tone map (srgb | game | lut) and bloom
(rig.post["bloom"], tone.bloom) on the composited HDR image, as view/tone.py documents it. Exposure, as the game does it
(docs/hdfx-shaders.md 3.1 step 5, calib r3 cb10[0].x = 0.073125): the game's HDR target is pre-exposed (the scene is
lit x exposure there), the Uber VS scales an effect by ExposureMultiplier x (PhysicalExposure ? PhysicalIntensity / pi x
cb10[0].x : 1) and writes that into it, and the tone pass maps the target. The viewer composes in scene-linear light (the
rig multiplies by its exposure in encode), so the effect's pre-exposed value is brought into it by 1 / the rig's exposure
(Post.target_scale) and cb10[0].x is Post.exposure (the rig's, else GAME_EXPOSURE). Over a game screenshot (bg), the
shot is taken back through the rig's tone curve to scene light, the effect added there, and the sum tone-mapped again
(hdr_over_bg), instead of blending in display light.

Floor: `floor=y` draws an invisible depth-only plane at height y before the scene depth is resolved, so soft particles
and depth tests see the ground the game has (Mephisto's disc sits 0.2 above it and fades against it).
"""
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import animmesh, aurastencil as _aura, cull as fxcull, gamekinds, mipbias, packers as pk, rspec
from . import colorvariant as _cv

# Ground irradiance of a game shot (rgb, scene units at the game's exposure): what the plate's albedo was lit with, so an
# effect light adds albedo x E = shot x E / PLATE_IRRADIANCE. Lane calib3 fitted (2.5, 2.25, 1.07) on Fire Bolt's ground
# pool alone, missile only (bench g4 firebolt_s, Blood Moor at night; B from one weak channel). Lane calib4 refitted the
# scale on two whole casts whose light records match the game's exactly (Fire Bolt: fire_cast_1 + missile; Charged
# Bolt: light_cast_1, Power 9000 at 7 units, + bolts; calib2 g7 / calib4 g3 captures): x1.5 gives the game's peak
# ground light round the caster (the calib bench's caster light: Fire Bolt 41.3 / 41.3, Charged Bolt 19.5 / 19.6), x1 was 1.2-1.7x
# too bright. Charged Bolt's white-blue light confirms B only weakly (the luma metric barely moves with it).
PLATE_IRRADIANCE = (3.75, 3.375, 1.605)

VIEW = Path(__file__).resolve().parents[1] / "view"
SHADER = VIEW / "fx.wgsl"
UFLOATS = 260          # 96 Uber/decal floats, the lane-mats block (mats_uniforms, 160), then xv (fx.wgsl, float 256)

RESOLVE_WGSL = """
@group(0) @binding(0) var t_dep: %s;
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
  var p = array<vec2<f32>, 3>(vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
  return vec4<f32>(p[i], 0.0, 1.0);
}
@fragment fn fs(@builtin(position) c: vec4<f32>) -> @location(0) vec4<f32> {
  return vec4<f32>(textureLoad(t_dep, vec2<i32>(c.xy), 0), 0.0, 0.0, 1.0);
}
"""

ANISO = 16

# depth-only floor: a square at height floor.y, floor.z units across, centred under the camera target (floor.x unused)
FLOOR_WGSL = """
struct FU { mvp: mat4x4<f32>, floor: vec4<f32>, };
@group(0) @binding(0) var<uniform> u: FU;
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
  var c = array<vec2<f32>, 6>(vec2(-1.0, -1.0), vec2(1.0, -1.0), vec2(1.0, 1.0), vec2(-1.0, -1.0), vec2(1.0, 1.0),
                              vec2(-1.0, 1.0));
  let h = u.floor.z * 0.5;
  return u.mvp * vec4<f32>(c[i].x * h + u.floor.x, u.floor.y, c[i].y * h + u.floor.w, 1.0);
}
@fragment fn fs() -> @location(0) vec4<f32> { return vec4<f32>(0.0); }
"""

# texture slot -> property names that fill it (first present wins)
SLOTS = {
    0: ("Blend.TextureBase", "Diffuse.DiffuseMap", "Decal.DiffuseMap", "Liquid.LiquidBase", "Emissive.EmissiveMap"),
    1: ("Blend.TextureBlend",),
    2: ("BlendMask.Mask",),
    3: ("AlphaMask.Mask",),
    4: ("Fresnel.Gradient",),
    5: ("VertexDisplacement.DisplacementMap",),
    6: ("UVDistortion.BlendDistort",),
    7: ("UVDistortion.MaskDistort",),
    8: ("AlphaRemap.AlphaMap",),
    9: ("GradientRemap.GradientMap",),
    10: ("Atlas.MotionVectorsMap",),
}
DEFAULT_TEX = {0: "white", 1: "white", 2: "white", 3: "white", 4: "white", 5: "black", 6: "flat", 7: "flat", 8: "white",
               9: "white", 10: "nrm"}
KIND_NAMES = {0: "additive-noalpha", 1: "additive", 2: "premultiplied", 3: "alpha", 4: "normal-pass"}


GAME_EXPOSURE = 0.073125    # cb10[0].x in calib r3 (Rogue Encampment, EV 4: view.rig.exposure_for_ev(4))


@dataclass
class Post:
    """The game's post chain for effects: the rig (exposure, tone, bloom) plus effect-side knobs."""
    rig: object = None                 # view.rig.Rig (None: plain sRGB, exposure 1)
    gain: float = 1.0                  # effect colour scale (viewer control)
    exposure: float | None = None      # the game's cb10[0].x (PhysicalExposure effects); None: the rig's, else GAME_EXPOSURE
    bare_alpha: bool = False           # additive draws also write luminance as alpha (renders on a transparent background)
    quality: object = None             # game settings of the mip bias (fx/mipbias.Quality; None: the visual profile / env)

    @classmethod
    def from_any(cls, post, light=None) -> "Post":
        """A Post from a Post, a view.rig.Rig, None, or the context lane's dict {bloom, tonemap, exposure}."""
        if post is None or isinstance(post, cls):
            return post or cls()
        if isinstance(post, dict):
            from ..view import gpu as G, rig as Rg
            r = Rg.Rig("fx", light or G.Light(), float(post.get("exposure") or 1.0), post.get("tonemap") or "srgb",
                       post=({"bloom": post["bloom"]} if post.get("bloom") else {}))
            return cls(r)
        return cls(post)

    def rig_exposure(self) -> float:
        return float(getattr(self.rig, "exposure", 1.0) or 1.0) if self.rig is not None else 1.0

    def game_exposure(self) -> float:
        """cb10[0].x: the exposure the game's target is in."""
        if self.exposure:
            return float(self.exposure)
        return self.rig_exposure() if self.rig is not None else GAME_EXPOSURE

    def target_scale(self) -> float:
        """Pre-exposed effect output -> the viewer's scene-linear target (gain / the exposure the rig applies)."""
        return float(self.gain) / self.rig_exposure()

    def physical_scale(self) -> float:
        """Exposure.PhysicalExposure: PhysicalIntensity / pi x cb10[0].x (VS 3.1 step 5)."""
        return self.game_exposure() / np.pi


@dataclass
class Material:
    """The Uber permutation and material constants of a renderer, by D2R's own rules (fx/shad.py, docs/hdfx-shaders.md
    section 2): blend mode m (0 additive without alpha, 1 additive, 2 premultiplied, 3 alpha blend, 4 mesh normal pass:
    not drawn), keywords, the cb4 ops and switches, textures by slot."""
    blend: int = 1
    multiplicative: bool = False
    soft: float = 0.0                                  # SoftnessDistance (0: off)
    keywords: dict = field(default_factory=dict)       # shad.uber_keywords: name -> "0"/"1"/...
    ops: tuple = (0, 0, 0, 0)                          # Blend.BlendOp, BlendMask.MaskOp, AlphaMask.MaskOp, AlphaMask.AlphaOnly
    inv: tuple = (0, 0, 0)                             # BlendMask.Invert, AlphaMask.Invert, flag +0x88 (0)
    fresnel: tuple = (0, 0, 0, 0)                      # Fresnel.BlendOp, ApplyToColor, ApplyToAlpha, DoubleSided
    physical: bool = False
    atlas: dict = field(default_factory=dict)          # subdiv, blending (0/1)
    default: bool = False                              # PopcornFX Default_* material (rgba diffuse x colour)
    textures: dict = field(default_factory=dict)       # slot -> logical path
    sort_offset: float = 0.0
    enable_mip_bias: bool = False                      # EnableMipBiasing
    general_mip_bias: float = 0.0                      # General.MipBias
    draw_resolution: int = 0                           # General.DrawResolution (constant input 0..2; cb4[6].z)
    mip_bias: float = 0.0                              # cb4[6].y at the default quality (fx/mipbias; the draw uses Post.quality)
    color_variant: float = 0.0                         # cb4[7].x VariantContribution (0: no COLOR_VARIANT; fx/colorvariant.py)
    notes: list = field(default_factory=list)
    # lane mats (docs/hdfx-shaders.md 8-12; mats_material fills these)
    family: str = "uber"                               # shad.material_family: uber smoke liquid dmesh standard stencil ...
    lit: int = 0                                       # u.lit.x mode (see view/fx.wgsl FxU.lit)
    wrap: float = 0.0                                  # NormalWrap.WrapFactor
    bend: float = 0.0                                  # NormalBend.NormalBendingFactor (billboard normals)
    entry: str = ""                                    # fragment entry point ("" = fs_fx)
    opaque: bool = False                               # m 4 meshes: drawn opaque (depth written)
    liquid: tuple = (0, 0, 0, 0, 0, 0, 1.0, 0.002)     # base op, colour-map op, base invert, map invert, colour map,
                                                       # dissolve, headlight diffuse, headlight specular
    cull: str = "none"                                 # "back" or "none": the game's cull mode (fx/cull.py)
    aura_stencil: bool = False                         # EnableAuraStencil (Uber class): fx/aurastencil.py
    dissolve: bool = False                             # AlphaRemap.Dissolve with ALPHA_REMAP (cb4[6].x; lane r3-shapes)
    tint: tuple = (1.0, 1.0, 1.0, 1.0)                 # u.tint: colour x alpha multiplier (a water model's albedo)
    water: tuple = ()                                  # fs_water: 23 floats, u[60:76] + u[257:260] (water_uniforms);
    #                                                    19..22 the flow, into u.ops / u.fres by time (water_time_uniforms)

    def on(self, kw) -> bool:
        return str(self.keywords.get(kw, "0")) not in ("0", "", "False")

    @classmethod
    def of(cls, r) -> "Material":
        from . import shad
        p = r.props
        get = p.get
        kind = r.kind if r.kind in ("mesh", "ribbon") else "billboard"
        m = cls()
        m.blend = shad.blend_mode(get, kind)
        m.keywords = shad.uber_keywords(get, kind, inputs=set(r.inputs) if r.inputs else None)
        m.multiplicative = m.on("POPCORN_MULTIPLICATIVE")
        m.soft = float(_num(get("SoftParticles.SoftnessDistance"))) if m.on("SOFT_PARTICLES") else 0.0
        m.ops = tuple(int(_num(get(k))) for k in ("Blend.BlendOp", "BlendMask.MaskOp", "AlphaMask.MaskOp")) + \
            (int(bool(_num(get("AlphaMask.AlphaOnly")))),)
        m.inv = (int(bool(_num(get("BlendMask.Invert")))), int(bool(_num(get("AlphaMask.Invert")))), 0)
        m.fresnel = (int(_num(get("Fresnel.BlendOp"))),) + tuple(int(bool(_num(get(k)))) for k in (
            "Fresnel.ApplyToColor", "Fresnel.ApplyToAlpha", "Fresnel.DoubleSided"))
        m.physical = bool(_num(get("Exposure.PhysicalExposure")))
        m.enable_mip_bias = bool(_num(get("EnableMipBiasing")))
        m.general_mip_bias = float(_num(get("General.MipBias")))
        m.draw_resolution = mipbias.draw_resolution(r, m.notes)
        if get("Atlas"):
            sd = get("Atlas.SubDiv") or (1, 1)
            sd = tuple(sd) if isinstance(sd, (tuple, list)) else (int(sd), int(sd))
            m.atlas = {"subdiv": (int(sd[0]) or 1, int(sd[1]) or 1), "blending": int(_num(get("Atlas.Blending"))),
                       # lane r3-atlasalpha: cb4[4].y and cb4[7].y of the atlas permutations (docs/hdfx-shaders.md 4)
                       "alpha_only": int(_num(get("AlphaMask.AtlasAlphaOnly"))) == 1 and r.kind == "billboard",
                       "flipbook": int(_num(get("Atlas.Flipbook"))) == 1}
        m.default = "uber" not in (r.material or "").lower() and r.kind in ("billboard", "ribbon")
        for slot, names in SLOTS.items():
            for n in names:
                v = get(n)
                if isinstance(v, str) and v:
                    m.textures[slot] = v
                    break
        m.sort_offset = float(_num(get("Transparent.CameraSortOffset")))
        m.dissolve = m.on("ALPHA_REMAP") and bool(_num(get("AlphaRemap.Dissolve")))
        mats_material(m, r)
        m.cull = fxcull.cull_mode(r.kind, r.material, get)
        m.color_variant = _cv.contribution(get, m.on("COLOR_VARIANT"))
        m.aura_stencil = m.family in ("uber", "smoke") and _aura.enabled(get)
        m.game_rules()
        return m

    def game_rules(self):
        """What the game does beyond the material's properties, as the RenderDoc capture shows it (calib r3, Mephisto):
        - a BlendMask whose mask is the default White texture is dropped: the skulls skirt draws PS #600 (FRESNEL SOFT
          ADDITIVE BLEND, no BLEND_MASK) with cb4[1].y 0, while its file has BlendMask on with White.texture;
        - mip bias cb4[6].y: D2R's rule from General.MipBias, General.DrawResolution and the game settings (fx/mipbias.py,
          read in the game's code and checked on 50 captured draws; EnableMipBiasing itself is not read by the game)."""
        mask = str(self.textures.get(2, "")).replace("\\", "/").lower()
        if self.on("POPCORN_BLEND_MASK") and mask.endswith("/default/white.texture"):
            self.keywords = dict(self.keywords, POPCORN_BLEND_MASK="0")
            self.notes.append("BlendMask with White.texture dropped (game: no BLEND_MASK permutation)")
        self.mip_bias = mipbias.material_bias(self.general_mip_bias, self.draw_resolution)

    def srgb(self, slot) -> bool:
        """Fresnel and gradient maps are sRGB (decoded to linear); noise, masks and distortion maps are linear data
        (notes 3.3); a Default material's diffuse map is colour."""
        return slot in (4, 9) or (slot == 6 and self.entry == "fs_water") or (
            slot == 0 and (self.default or self.family in ("dmesh", "standard", "decal")))


def _num(v):
    if isinstance(v, (tuple, list)):
        return v[0] if v else 0
    if v is None:
        return 0
    return v


@dataclass
class FxDraw:
    kind: str                     # mesh | billboard | ribbon | decal
    renderer: int
    layer: str
    material: Material
    verts: np.ndarray             # (n, packers.FLOATS)
    entry: str = "fs_fx"
    decal: np.ndarray | None = None   # world -> decal box
    decal_rec: object = None          # fx/decal.Record (the game's decal record; fs_decal_mul / fs_decal_add)
    name: str = ""
    margin: float = 0.0           # vertex displacement reach (bounds)
    cast: float | None = None     # shadow caster: its alpha threshold (view/shadow.py caster_threshold); None: casts none
    time: float = 0.0             # the effect's age (SimState.t): the water's flow clock (lane r4-glow)
    gpu: dict = field(default_factory=dict)

    def points(self) -> np.ndarray:
        P = self.verts[:, 0:3]
        if not len(P):
            return np.zeros((0, 3))
        lo, hi = P.min(0) - self.margin, P.max(0) + self.margin
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])

    def sort_key(self, cam) -> float:
        P = self.verts[:, 0:3]
        return float(cam.depth(P.mean(0))) + self.material.sort_offset if len(P) else 0.0


class FxRenderer:
    def __init__(self, gpu, vfs, post: Post | None = None):
        self.gpu = gpu
        self.vfs = vfs
        self.post = post or Post()
        self.notes = []
        self.lights = []                  # packers.FxLight of the last prepare
        self.sounds = []                  # SimState.sounds of the last prepare (fx/gamekinds.py: silent in D2R)
        self.show_sounds = False          # draw a marker where a sound renderer would play (viewer only)
        self._mesh = {}
        self._mesh_sk = {}
        self._texinfo = {}
        self._pipes = {}
        self._ztex = {}
        self._setup()

    # ---- GPU objects -------------------------------------------------------------------------------------------------
    def _setup(self):
        g = self.gpu
        wgpu = g.wgpu
        dev = g.device
        T = wgpu.ShaderStage
        tex = {"sample_type": "float", "view_dimension": "2d"}
        self.bgl = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.VERTEX | T.FRAGMENT, "buffer": {"type": "uniform"}},
            {"binding": 1, "visibility": T.VERTEX | T.FRAGMENT, "sampler": {"type": "filtering"}},
            {"binding": 2, "visibility": T.FRAGMENT, "sampler": {"type": "filtering"}},
            *[{"binding": b, "visibility": T.VERTEX | T.FRAGMENT, "texture": tex} for b in range(3, 14)],
            {"binding": 14, "visibility": T.FRAGMENT, "texture": {"sample_type": "unfilterable-float", "view_dimension": "2d"}}])
        from ..view import shadow as _shadow
        self.layout = dev.create_pipeline_layout(bind_group_layouts=[self.bgl, g.shadow.bgl])   # 1: view/shadow.py maps
        self.module = dev.create_shader_module(code=_shadow.patch(SHADER.read_text(encoding="utf-8")))
        ms = g.msaa > 1
        self.bgl_z = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.FRAGMENT, "texture": {"sample_type": "depth", "view_dimension": "2d",
                                                                 "multisampled": ms}}])
        zmod = dev.create_shader_module(code=RESOLVE_WGSL % ("texture_depth_multisampled_2d" if ms else "texture_depth_2d"))
        self.p_resolve = dev.create_render_pipeline(
            layout=dev.create_pipeline_layout(bind_group_layouts=[self.bgl_z]),
            vertex={"module": zmod, "entry_point": "vs"}, primitive={"topology": "triangle-list"},
            fragment={"module": zmod, "entry_point": "fs", "targets": [{"format": "r32float"}]})
        # the game's material samplers (calib r3): wrap, anisotropic x16, LOD 0-16; the Fresnel gradient clamp-to-edge x16
        self.s_rep = dev.create_sampler(address_mode_u="repeat", address_mode_v="repeat", mag_filter="linear",
                                        min_filter="linear", mipmap_filter="linear", max_anisotropy=ANISO)
        self.s_clamp = dev.create_sampler(address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge",
                                          mag_filter="linear", min_filter="linear", mipmap_filter="linear",
                                          max_anisotropy=ANISO)
        fmod = dev.create_shader_module(code=FLOOR_WGSL)
        self.bgl_f = dev.create_bind_group_layout(entries=[
            {"binding": 0, "visibility": T.VERTEX, "buffer": {"type": "uniform"}}])
        self.p_floor = dev.create_render_pipeline(
            layout=dev.create_pipeline_layout(bind_group_layouts=[self.bgl_f]),
            vertex={"module": fmod, "entry_point": "vs"}, primitive={"topology": "triangle-list", "cull_mode": "none"},
            depth_stencil={"format": "depth32float", "depth_write_enabled": True, "depth_compare": "less"},
            multisample={"count": g.msaa},
            fragment={"module": fmod, "entry_point": "fs", "targets": [{"format": "rgba16float", "write_mask": 0}]})
        px = {"white": (255, 255, 255, 255), "black": (0, 0, 0, 255), "flat": (128, 128, 128, 255),
              "nrm": (128, 128, 255, 128),           # a flat DXT5nm normal (x in alpha, y in green)
              "orm": (255, 255, 0, 255),             # occlusion 1, roughness 1, metal 0, no emissive
              "water_flow": (255, 127, 0, 255),     # default_WATER.texture RG
              "orm_water": (255, 26, 0, 255)}        # a water model (WaterMaterial Roughness 0.1)
        self._defaults = {k: g.texture([np.full((4, 4, 4), v, np.uint8)], srgb=k == "water_flow") for k, v in px.items()}

    def _pipe(self, entry, blend, cull=("none", "ccw"), stencil=None):
        key = (entry, blend) + tuple(cull) + (() if stencil is None else ("aura",))
        if key in self._pipes:
            return self._pipes[key]
        A = self.gpu.wgpu.VertexFormat
        attrs, off = [], 0
        for loc, (_n, k) in enumerate(pk.LAYOUT):
            attrs.append({"format": {3: A.float32x3, 4: A.float32x4}[k], "offset": off * 4, "shader_location": loc})
            off += k
        vb = [{"array_stride": pk.FLOATS * 4, "step_mode": "vertex", "attributes": attrs}]
        one = {"src_factor": "one", "dst_factor": "one", "operation": "add"}
        over = {"src_factor": "one", "dst_factor": "one-minus-src-alpha", "operation": "add"}
        keep = {"src_factor": "zero", "dst_factor": "one", "operation": "add"}
        bs = {0: {"color": one, "alpha": one}, 1: {"color": one, "alpha": one}, 2: {"color": over, "alpha": over},
              3: {"color": over, "alpha": over}, 4: {"color": over, "alpha": over},
              -1: {"color": {"src_factor": "dst", "dst_factor": "zero", "operation": "add"}, "alpha": keep}}.get(blend)
        opaque = False
        tgt = {"format": "rgba16float"}
        if bs:
            tgt["blend"] = bs
        p = self.gpu.device.create_render_pipeline(
            layout=self.layout, vertex={"module": self.module, "entry_point": "vs_fx", "buffers": vb},
            primitive={"topology": "triangle-list", "cull_mode": cull[0], "front_face": cull[1]},
            depth_stencil={"format": "depth32float", "depth_write_enabled": opaque,
                           "depth_compare": "less" if opaque else ("always" if entry.startswith("fs_decal") else "less-equal"),
                           **(stencil or {})},
            multisample={"count": self.gpu.msaa},
            fragment={"module": self.module, "entry_point": entry, "targets": [tgt]})
        self._pipes[key] = p
        return p

    def _tex(self, slot, path, srgb=False):
        """The view of a texture path for a slot (sRGB-decoded when `srgb`), or the slot's default."""
        if not path or path.startswith("@"):                 # "@name": one of the default textures by name
            return self._defaults[path[1:] if path else DEFAULT_TEX[slot]]
        key = (bool(srgb), path)
        t = self._texinfo.get(key, False)
        if t is False:
            from ..view import scene
            try:
                if not self.vfs.exists(path):
                    raise FileNotFoundError(path)
                t = scene.read_texture(self.vfs, path, srgb=bool(srgb))
            except (FileNotFoundError, KeyError, ValueError) as e:
                self.notes.append("texture %s: %s; default used" % (path, e.__class__.__name__))
                t = None
            self._texinfo[key] = t
        if t is None:
            return self._defaults["water_flow" if slot == 6 and srgb else DEFAULT_TEX[slot]]
        return self.gpu.texture(t.mips, srgb=t.srgb)

    def _load_mesh(self, path):
        if path not in self._mesh:
            from ..view import scene
            try:
                part = scene.load_model_part(self.vfs, path, "fxmesh")
                self._mesh[path] = part.meshes
                self._mesh_sk[path] = part.skeleton             # for Animation (fx/animmesh.py)
            except FileNotFoundError:
                self.notes.append("mesh %s not found" % path)
                self._mesh[path] = []
        return self._mesh[path]

    # ---- prepare -----------------------------------------------------------------------------------------------------
    def prepare(self, effect, state, cam, transform=None, only=None, hide=()) -> list:
        """FxDraws of an effect's renderers for a SimState. `transform` (4x4): the effect's placement; the simulation
        already placed the particles (SimContext.transform), so only the mesh orientations are turned by it. `only`:
        renderer indices to draw; `hide`: renderer indices left out."""
        self.lights = []
        self.sounds = list(getattr(state, "sounds", None) or [])
        draws = []
        basis = cam.basis()
        T = np.eye(4) if transform is None else np.asarray(transform, float)
        if not isinstance(effect, rspec.RenderEffect):          # a lane-format graph.Effect (the MCP api passes one)
            effect = rspec.from_graph(effect)
        for r in effect.renderers:
            if not r.enabled or (only is not None and r.index not in only) or r.index in hide:
                continue
            if not gamekinds.drawn_by_game(r.kind, r.material):      # Distortion, triangle, sound: no D2R material
                self.notes.append("renderer %d: %s %s has no D2R material, not drawn (as the game)"
                                  % (r.index, r.kind, (r.material or "").split("/")[-1]))
                continue
            self.notes.extend("renderer %d: %s" % (r.index, n) for n in gamekinds.notes_for(r))
            batch = layer_batch(state, r)
            if batch is None:
                continue
            inputs = inputs_for(r, batch)
            n = _count(inputs, batch)
            if n == 0:
                continue
            mat = Material.of(r)
            self.notes.extend("renderer %d: %s" % (r.index, n) for n in mat.notes)
            if mat.family is None:
                self.notes.append("renderer %d: material %s has no D2R class, not drawn" % (r.index, r.material))
                continue
            if mat.blend == 4 and r.kind != "decal" and not mat.opaque:
                self.notes.append("renderer %d: blend mode 4 (a normal pass in the game) not drawn" % r.index)
                continue
            pblock = pk.particle_block(inputs, n, mat.family)
            if mat.color_variant:
                self._color_variant(r, inputs, n, pblock)
            pos = _arr(inputs, ("Position",), n, 3, batch)
            if r.kind == "mesh":
                d = self._prep_mesh(r, mat, inputs, n, pos, pblock, T, batch)
            elif r.kind == "billboard":
                d = self._prep_billboard(r, mat, inputs, n, pos, pblock, basis, cam)
            elif r.kind == "ribbon":
                d = self._prep_ribbon(r, mat, inputs, n, pos, pblock, basis)
            elif r.kind == "light":
                if not getattr(state, "lights", None):      # SimState.lights (lane vm) is preferred: below
                    self._prep_light(r, inputs, n, pos)
                d = None
            elif r.kind == "decal":
                d = self._prep_decal(r, mat, inputs, n, pos, pblock, basis, T)
            else:
                self.notes.append("renderer %d: kind %s not drawn" % (r.index, r.kind))
                d = None
            if d is not None:
                d = d if isinstance(d, list) else [d]
                cast = _caster_threshold(r)
                for x in d:
                    me = x.material.entry or mat.entry          # a draw's own material wins (a water model: fs_water)
                    x.entry = me if (me and x.entry == "fs_fx") else x.entry
                    x.cast = cast if x.kind != "decal" else None
                    x.time = float(getattr(state, "t", 0.0))     # the water's flow clock (lane r4-glow)
                draws.extend(d)
        self._state_lights(effect, state)
        if getattr(self, "show_sounds", False) and self.sounds:
            v = gamekinds.sound_markers(self.sounds, basis)
            if v is not None:
                draws.append(FxDraw("billboard", -1, "sound", Material(blend=1, default=True, family="default"), v,
                                    name="sound-markers"))
        # decals first, in particle order and in mul/add pairs: the game blends them into the surface before lighting
        dec = [d for d in draws if d.decal_rec is not None]
        return dec + sort_draws([d for d in draws if d.decal_rec is None], cam)

    def _color_variant(self, r, inputs, n, pblock):
        """ColorVariant (fx/colorvariant.py): each particle's TransformIndex -> its table entry, into the vertex block."""
        if getattr(self, "cv_table", None) is None:
            self.cv_table = _cv.Table.load(self.vfs)
        k = _cv.fill_block(pblock, inputs, n, self.cv_table, True)
        if k and not self.cv_table.loaded:
            self.notes.append("renderer %d: colour variant table not found (%s): drawn untinted; add --root casc"
                              % (r.index, _cv.SHIFT_PATH))

    def _state_lights(self, effect, state):
        """SimState.lights (docs/hdfx.md section 2): position, color (rgba), power, range, on, per particle, as the game's
        point lights (packers.lights_from: colour x power / (4 pi), range, exponent 8 x the renderer's
        LightAttenuation.AttenuationSteepness; read from the game's light records, calib3)."""
        steep = {r.index: float(_num(r.get("LightAttenuation.AttenuationSteepness", 1.0)) or 1.0) for r in effect.renderers}
        for L in getattr(state, "lights", None) or []:
            on = np.asarray(L.get("on", []), bool).reshape(-1)
            pos = np.asarray(L.get("position", np.zeros((0, 3))), float).reshape(-1, 3)
            if not len(pos):
                continue
            n = len(pos)
            on = on if len(on) == n else np.ones(n, bool)
            col = np.asarray(L.get("color", np.ones((n, 4))), float).reshape(n, -1)
            power = np.asarray(L.get("power", np.ones(n)), float).reshape(n)
            rng = np.asarray(L.get("range", np.full(n, 5.0)), float).reshape(n)
            col = np.hstack([col[:, :3], np.ones((n, 1))])
            self.lights.extend(pk.lights_from(pos[on], col[on], rng[on], power[on], steep.get(L.get("renderer"), 1.0)))

    def _prep_mesh(self, r, mat, inputs, n, pos, pblock, T, batch=None):
        path = r.get("General.Mesh")
        if not path and r.get("ModelInstanceTracking") and getattr(self, "tracked", None):
            from . import modeltrack                  # the unit's own posed model (lane r4-glow)
            return modeltrack.prep_tracked(self, r, mat, n, pblock)
        meshes = self._load_mesh(path) if path else []
        if not meshes:
            return None
        scale = _arr(inputs, ("Scale",), n, 3, None, 1.0)
        q = _arr(inputs, ("Orientation",), n, 4, None, None)
        X = pk.mesh_transforms(pos, scale, q)
        oi = (getattr(r, "inputs", None) or {}).get("Orientation")
        if oi is None or getattr(oi, "const", None) is not None:
            # a constant (or no) orientation is effect-local: turned by the placement. A script-written Orientation is
            # already world space (calib2: Fire Bolt's MeshFlameHead from the sim equals the capture's instance matrix
            # to 0.002; turning it again crumpled the head)
            X[:, :3, :3] = T[:3, :3] @ X[:, :3, :3]
        disp = float(np.max(pblock[:, pk.OFF["sc"][0] - 10 + 3])) if mat.on("VERTEX_DISPLACEMENT") else 0.0
        anim = animmesh.skin_groups(self, r, path, meshes, n, batch)     # Animation: [(skinned meshes, particles)]
        out = []
        for mi, m in enumerate(meshes):
            va = None
            if getattr(m, "col", None) is not None and (mat.on("VERTEX_ALPHA") or r.kind == "mesh"):
                # VERTEX_ALPHA: the game sets it from a particle input flag (docs/hdfx-shaders.md 2); the RenderDoc
                # capture (calib r3) shows it on Mephisto's skirts. A mesh with vertex colours gets it here.
                va = np.asarray(m.col, float)[:, 3] / 255.0
                self.notes.append("renderer %d: VERTEX_ALPHA from the mesh's vertex colours" % r.index)
            dm = None
            if disp and getattr(m, "col", None) is not None:
                dm = np.asarray(m.col, float)[:, 0] / 255.0       # vertex colour R masks the displacement (VS 1733)
            if anim is None:
                v = pk.pack_mesh(m.pos, m.nrm, m.uv, m.tris, X, pblock, va, dm)
            else:
                v = animmesh.pack_groups(anim, mi, m, X, pblock, va, dm)
            out.append(FxDraw("mesh", r.index, r.layer, standard_material(mat, m), v, name="%d:%s" % (r.index, m.name),
                              margin=abs(disp)))
        return out

    def _prep_billboard(self, r, mat, inputs, n, pos, pblock, basis, cam):
        size = _arr(inputs, ("Size", "Size2", "General.Size"), n, 2, None, 1.0)
        rot = _arr(inputs, ("Rotation",), n, 1, None, 0.0)[:, 0]
        axis = _arr(inputs, ("Axis", "Velocity"), n, 3, None, 0.0)
        axis2 = _arr(inputs, ("Axis2",), n, 3, None, None)
        mode = int(_num(r.get("General.BillboardingMode", 0)))
        rects = pk.atlas_rects(mat.atlas["subdiv"]) if mat.atlas else None
        tid = _arr(inputs, ("TextureID", "Atlas.TextureID"), n, 1, None, 0.0)[:, 0]
        order = pk.sort_order(cam.depth(pos), {0: 0, 1: 0, 2: 3, 3: 2}.get(mat.blend, 0))
        v = pk.pack_billboards(mode, pos, size, rot, basis, pblock, axis, axis2,
                               float(_num(r.get("General.AxisScale", 1.0)) or 1.0),
                               float(_num(r.get("General.AspectRatio", 1.0)) or 1.0), tid, rects,
                               bool(mat.atlas and mat.atlas["blending"] > 0), order, bend=mat.bend,
                               alpha_only_subdiv=mat.atlas["subdiv"] if mat.atlas and mat.atlas.get("alpha_only") else None)
        # (packers.sort_order takes the PopcornFX Transparent.Type numbering: 2 alpha, 3 premultiplied sort)
        return FxDraw("billboard", r.index, r.layer, mat, v, name="%d:billboard" % r.index)

    def _prep_ribbon(self, r, mat, inputs, n, pos, pblock, basis):
        width = _arr(inputs, ("Width", "General.Width"), n, 1, None, 0.25)[:, 0]
        axis = _arr(inputs, ("Axis",), n, 3, None, None)
        u = _arr(inputs, ("TextureU", "CustomTextureU.TextureU"), n, 1, None, None)
        mode = int(_num(r.get("General.BillboardingMode", 0)))
        fu, fv = bool(r.get("TextureUVs.FlipU")), bool(r.get("TextureUVs.FlipV"))
        if r.get("TextureUVs") is False:                       # the switch off: the flags are not read (0x14100e4c5)
            fu = fv = False
        rot = bool(r.get("TextureUVs.RotateTexture")) and r.get("TextureUVs") is not False
        chain = inputs.get(CHAIN)
        if chain is None or len(np.unique(chain[:, 0])) < 2:
            v = pk.pack_ribbon(pos, width, basis, pblock, mode, axis, None if u is None else u[:, 0], fu, fv, rot)
            return FxDraw("ribbon", r.index, r.layer, mat, v, name="%d:ribbon" % r.index)
        # one strip per parent particle (PopcornFX ribbons link the particles one parent spawned; lane cast: the
        # Lightning cast overlay's branches, one per @391 trail, drew as one strip across all of them)
        pb = np.asarray(pblock)
        parts = []
        _keys, first = np.unique(chain[:, 0], return_index=True)
        for key in chain[np.sort(first), 0]:
            m = chain[:, 0] == key
            if m.sum() < 2:
                continue
            sub = (lambda a: None if a is None else (a[m] if len(a) == n else a))
            parts.append(pk.pack_ribbon(pos[m], width[m], basis, pb[m] if len(pb) == n else pb, mode, sub(axis),
                                        None if u is None else u[m][:, 0], fu, fv, rot))
        v = np.concatenate(parts) if parts else np.zeros((0, pk.FLOATS), np.float32)
        return FxDraw("ribbon", r.index, r.layer, mat, v, name="%d:ribbon" % r.index)

    def _prep_light(self, r, inputs, n, pos):
        col = _find(inputs, "Color", n, 4, (1.0, 1.0, 1.0, 1.0))
        rad = _find(inputs, ("Range", "Radius"), n, 1, (5.0,))[:, 0]
        inten = _find(inputs, "Intensity", n, 1, (1.0,))[:, 0]
        steep = float(_num(r.get("LightAttenuation.AttenuationSteepness", 1.0)) or 1.0)
        self.lights.extend(pk.lights_from(pos, col, rad, inten, steep))

    def _prep_decal(self, r, mat, inputs, n, pos, pblock, basis, T):
        """Two draws per decal (fx/decal.py): the relighting factor (dst x src) and the added light."""
        from . import decal as dc
        if "Diablo_Decal" not in (r.material or ""):            # kind 4 Default_Decal: no D2R class (picker 0xec3b60)
            self.notes.append("renderer %d: %s has no D2R decal class, not drawn" % (r.index, r.material))
            return None
        mat.textures = dc.textures(r.props)
        out = []
        self._decal_calls = getattr(self, "_decal_calls", 0) + 1    # game list order: effect by effect (decal_pass)
        for i, rec in enumerate(dc.records(r.props, inputs, n, pos)):
            if rec is None:
                continue
            rec.seq = (self._decal_calls, i)
            v = pk.decal_quad(rec.world_to_box, basis, pblock[i])
            for entry in ("fs_decal_mul", "fs_decal_add"):
                out.append(FxDraw("decal", r.index, r.layer, mat, v, entry=entry, decal=rec.world_to_box,
                                  decal_rec=rec, name="%d:decal" % r.index))
        return out

    # ---- render ------------------------------------------------------------------------------------------------------
    def scene_light(self, light):
        """The scene light with this effect's lights added as point lights."""
        if not self.lights:
            return light
        return replace(light, points=tuple(light.points) + tuple(L.point() for L in self.lights))

    def render(self, draws, cam, size, base=None, light=None, post=None, origin=None, ss=2, bg=None, linear=False,
               floor=None, plate_light=PLATE_IRRADIANCE):
        """8-bit RGBA (H, W, 4) of `base` (gpu.Prepared or None) with the effect draws composited, post-processed by
        post.rig (exposure, tone, bloom). `linear`: the linear premultiplied float image before the post chain.
        `floor`: height of an invisible depth-only ground plane (None: none); `bg` (linear (h, w, 3) or (3,), as
        cli.parse_bg gives): a game screenshot under the render, composited in scene light (hdr_over_bg).
        `plate_light`: the ground irradiance (rgb, scene units) the `bg` shot was lit with, or a fx.lighting.GameLighting
        (its per-pixel E(P) from the scene's data). The effect's lights relight the shot using depth units where
        visible and the ground at y = `floor` or 0 elsewhere. None or 0: the shot stays as it is."""
        from ..view import gpu as G
        post = Post.from_any(post, light) if post is not None else self.post
        light = light or (post.rig.light if post.rig is not None else G.Light())
        light = self.scene_light(light)
        prep = base if base is not None else G.Prepared([])
        W, H = size
        origin = origin or (W / 2.0, H * 0.75)
        from . import decal_pass                    # every decal in one pass, as the terrain shader (lane r3-decal2)
        draws = decal_pass.group(self, draws)
        ov = FxOverlay(self, draws, post, floor)
        ov.light = light
        gain = None
        scene_plate = hasattr(plate_light, "plate_gain")       # fx.lighting.GameLighting: E(P) from the game's data
        shot = bg is not None and post.rig is not None and np.ndim(bg) == 3
        vis_c = None
        if shot and scene_plate:
            # the shadow maps feed the scene's `visibility` (lane r4-lighting3): E0 under the caster's own shadow (in the
            # shot), E1 under the caster's and the effect's; one gain for the decals' plate and the composite below
            P, n = self.plate_surface(prep, cam, origin, size, floor)
            surface = (P, n) if prep.occluders else None
            vis, vis_c = self.plate_shadow_terms(prep, light, draws, cam, origin, size, floor or 0.0, ov, surface=surface)
            gain = plate_light.plate_gain(P, [L.point() for L in self.lights], visibility=vis_c, visibility_fx=vis, n=n)
        elif shot and self.lights and plate_light is not None and np.any(np.asarray(plate_light, float) > 0):
            P, n = self.plate_surface(prep, cam, origin, size, floor)
            gain = plate_gain(P, [L.point() for L in self.lights], plate_light, n=n)
        if shot and any(d.decal_rec is not None for d in draws):
            from . import decal as dc               # decals relight the shot's ground (fs_decal_add, plate mode)
            ov.plate = dc.plate_view(self.gpu, plate_scene(bg, post.rig, gain))
            if scene_plate:                         # the scene's ground irradiance under the effect's origin
                v0 = None
                if vis_c is not None:               # the shot's own shadow terms at the origin pixel
                    ox, oy = int(np.clip(round(origin[0]), 0, W - 1)), int(np.clip(round(origin[1]), 0, H - 1))
                    v0 = vis_c[oy:oy + 1, ox:ox + 1].reshape(1, -1)
                ov.plate_irr = tuple(float(x) for x in np.asarray(
                    plate_light.irradiance(np.zeros((1, 3)), visibility=v0)).reshape(-1)[:3])
            else:
                ov.plate_irr = tuple(plate_light) if plate_light is not None and np.any(np.asarray(plate_light) > 0) \
                    else PLATE_IRRADIANCE
        ov.units = _aura.units_of(prep)
        lin = self.gpu.render(prep, cam, size, origin, "lit", light, True, ss, None, None, None, overlay=ov, linear=True)
        if linear:
            return lin
        enc = post.rig.encode if post.rig is not None else None
        if bg is not None and post.rig is not None:     # `gain`: the plate gain, made above
            if shot and not scene_plate:            # a fixed plate irradiance: the shadows' ground gain on top
                sg = self.gpu.shadow.ground_gain(light, cam, origin, (W, H), floor or 0.0)
                if sg is not None:               # the shadows of the unit and the effect on the shot's ground
                    gain = sg if gain is None else gain * sg
            return hdr_over_bg(lin, bg, post.rig, gain)
        return G.to_uint8(lin, "lit", bg, enc)

    def plate_surface(self, prep, cam, origin, size, floor):
        """World position and shading normal of the plate's ground and depth units at each pixel centre."""
        from ..view import gpu as G
        P = ground_points(cam, origin, size, floor or 0.0)
        if not prep.occluders:
            return P, (0.0, 1.0, 0.0)
        units = G.Prepared([replace(d, blend=0.0) for d in prep.occluders])
        drange = self.gpu.depth_range(units, cam)
        args = (units, cam, size, origin)
        depth = self.gpu.render(*args, "depth", glow=False, linear=True)
        normal = self.gpu.render(*args, "normals", glow=False, linear=True)
        hit = depth[..., 3] >= 0.5
        dmin, dmax = drange
        w = cam.basis()[2]
        # Debug passes resolve against transparent black. Divide by coverage before decoding their values.
        d = dmin + (dmax - dmin) * depth[hit, 0] / depth[hit, 3]
        shift = d - P[hit] @ w
        surface = P[hit] + shift[:, None] * w
        if floor is not None:                       # the ground plane wins when closer to the camera
            visible = shift >= 0.0
            hit[hit] = visible
            surface = surface[visible]
        P[hit] = surface
        n = np.zeros_like(P)
        n[..., 1] = 1.0
        v = normal[hit, :3] / normal[hit, 3:4] * 2.0 - 1.0
        n[hit] = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
        return P, n

    def plate_shadow_terms(self, prep, light, draws, cam, origin, size, y, overlay=None, *, surface=None):
        """(vis, vis_c) plate shadow terms (G, slice 0, slice 1, cube), with the caster and the
        effect's casters (`overlay`'s draws with `cast`) and with the caster alone (the shadow the game shot already
        holds). The maps are encoded here, before the pass (which encodes them again with the effect's casters).
        (None, None) when shadows are off."""
        sm = self.gpu.shadow

        def terms(ov):
            enc = self.gpu.device.create_command_encoder()
            spec = sm.encode(enc, prep, light, ov)
            self.gpu.device.queue.submit([enc.finish()])
            return None if spec is None else sm.ground_vis(light, cam, origin, size, y, surface=surface)
        cast = overlay is not None and any(getattr(d, "cast", None) is not None for d in overlay.draws)
        vis_c = terms(None)
        if vis_c is None:
            return None, None
        return (terms(overlay) if cast else vis_c), vis_c

    def ztex(self, w, h):
        key = (w, h)
        if key not in self._ztex:
            U = self.gpu.wgpu.TextureUsage
            if len(self._ztex) > 4:
                self._ztex.clear()
            self._ztex[key] = self.gpu.device.create_texture(size=(w, h, 1), format="r32float",
                                                             usage=U.RENDER_ATTACHMENT | U.TEXTURE_BINDING)
        return self._ztex[key]


def inverse_encode(encode, display, lo: float = 1e-5, hi: float = 64.0, n: int = 4096):
    """Scene-linear values that `encode` (scene-linear rgb -> display sRGB, a rig's exposure + tone) maps to `display`
    (per channel, through the grey curve: encode is monotonic on greys). Display values at or above the curve's top give
    its top input (a clipped shot holds no more)."""
    xs = np.concatenate([[0.0], np.geomspace(lo, hi, n)])
    ys = np.asarray(encode(np.repeat(xs[:, None], 3, 1)), float)[:, 1]
    ys = np.maximum.accumulate(ys)
    return np.interp(np.asarray(display, float), ys, xs)


def ground_points(cam, origin, size, y=0.0):
    """(H, W, 3) world points of the plane at height y under each pixel centre of an image `size` = (W, H) whose
    world origin lands on pixel `origin` (camera.OrthoCamera.to_pixels inverted)."""
    W, H = size
    r, u, w = (np.asarray(v, float) for v in cam.basis())
    xs = (np.arange(W) + 0.5 - origin[0]) / cam.k
    ys = -(np.arange(H) + 0.5 - origin[1]) / cam.k
    a, b = np.meshgrid(xs, ys)
    P = a[..., None] * r + b[..., None] * u
    c = (y - P[..., 1]) / (w[1] if abs(w[1]) > 1e-9 else 1e-9)
    return P + c[..., None] * w


def plate_gain(P, points, irradiance=PLATE_IRRADIANCE, n=(0.0, 1.0, 0.0)):
    """(H, W, 3) factor 1 + E / irradiance for surface points P with normal n lit by gpu.Light point dicts (colour per the
    point's own form, packers.light_falloff), E the added irradiance."""
    from . import packers as pk
    E = np.zeros(P.shape[:-1] + (3,))
    for p in points:
        if p.get("directional"):
            continue
        dv = np.asarray(p["pos"], float) - P
        d = np.sqrt((dv * dv).sum(-1))
        nl = np.clip((dv * n).sum(-1) / np.maximum(d, 1e-4), 0.0, 1.0)
        f = pk.light_falloff(d, float(p["radius"]), float(p.get("atten", 1.0))) * nl
        E += f[..., None] * np.asarray(p["color"], float)
    return 1.0 + E / np.maximum(np.asarray(irradiance, float), 1e-6)


def plate_scene(bg, rig, gain=None):
    """The shot (linear display light) back to scene light through the rig, times `gain` (plate_gain)."""
    from ..view import gpu as G
    scene = inverse_encode(rig.encode, G.srgb_encode(np.clip(np.asarray(bg, float), 0.0, 1.0)))
    return scene * gain if gain is not None else scene


def hdr_over_bg(lin, bg, rig, gain=None):
    """The game's order over a screenshot: the shot (bg, linear display light as cli.parse_bg gives it) back to scene
    light through the rig (inverse_encode), times `gain` ((H, W, 3), plate_gain: the effect's lights on the shot's
    ground), the render (premultiplied scene light: unit, effects) over it, then the rig's tone map (and bloom) of the
    sum. 8-bit RGBA, alpha 255."""
    from ..view import gpu as G
    c, a = lin[..., :3], np.clip(lin[..., 3:4], 0.0, 1.0)
    scene = plate_scene(np.broadcast_to(np.asarray(bg, float), c.shape), rig, gain)
    comp = c + scene * (1.0 - a)
    if getattr(rig, "game_bloom", None) and rig.game_bloom():
        # the shot already holds the game's bloom of its own scene: add only what the effect changes (the chain is
        # linear after its cutoff, so bloom(comp) - bloom(shot) is the effect's part)
        B = rig.bloom(comp) - rig.bloom(scene)
        disp = np.clip(rig.encode(np.maximum(comp + B, 0.0)), 0.0, 1.0)
        return np.round(np.concatenate([disp, np.ones_like(a)], -1) * 255.0).astype(np.uint8)
    full = np.concatenate([comp, np.ones_like(a)], -1)
    da = rig.encode_image(full) if hasattr(rig, "encode_image") else None
    disp = da[0] if da is not None else np.clip(rig.encode(comp), 0.0, 1.0)
    return np.round(np.concatenate([disp, np.ones_like(a)], -1) * 255.0).astype(np.uint8)


def light_coverage(lin, encode=None):
    """Additive light where the scene is empty (alpha 0) as coverage, so it reaches a background composited later
    (gpu.to_uint8 divides colour by alpha): alpha' = alpha + (1 - alpha) x the light's display level, the same over-blend
    as view.rig's bloom halo. The colour (premultiplied) is unchanged."""
    from ..view import gpu as G
    c, a = lin[..., :3], np.clip(lin[..., 3:4], 0.0, 1.0)
    extra = np.maximum(c - a, 0.0)                     # light beyond what the covered part (straight <= 1) explains
    d = np.clip(encode(extra) if encode is not None else G.srgb_encode(np.clip(extra, 0.0, 1.0)), 0.0, 1.0)
    h = np.clip(G.srgb_decode(d).max(-1, keepdims=True), 0.0, 1.0)
    out = lin.copy()
    out[..., 3:4] = np.maximum(a, a + (1.0 - a) * h)
    return out


def sort_draws(draws, cam) -> list:
    """Back to front by draw (camera.depth grows towards the camera); additive draws commute, so only the blended ones
    care, but one order keeps the image stable."""
    return sorted(draws, key=lambda d: d.sort_key(cam))


class FxOverlay:
    """The `overlay` of gpu.Renderer.render_rect: resolve the scene depth, then draw the effect draws."""

    def __init__(self, fxr: FxRenderer, draws, post: Post, floor=None):
        self.fxr = fxr
        self.draws = [d for d in draws if len(d.verts)]
        self.post = post
        self.floor = floor

    def points(self):
        pts = [d.points() for d in self.draws]
        if self.floor is not None:
            y, half = self.floor_patch(pts)
            pts.append(np.array([[x, y, z] for x in (-half, half) for z in (-half, half)]))
        return np.concatenate(pts) if pts else np.zeros((0, 3))

    def floor_patch(self, pts=None) -> tuple:
        """(height, half size): the floor square reaches 4 units past the effect draws (at least 8), inside the depth
        range the renderer sets from points()."""
        pts = [d.points() for d in self.draws] if pts is None else pts
        y = float(self.floor[0] if isinstance(self.floor, (tuple, list)) else self.floor)
        P = np.concatenate(pts) if pts else np.zeros((0, 3))
        half = float(np.abs(P[:, [0, 2]]).max()) + 4.0 if len(P) else 8.0
        return y, max(half, 8.0)

    def _bind(self, d, zview):
        f = self.fxr
        g = f.gpu
        if "vbuf" not in d.gpu:
            d.gpu["vbuf"] = g.device.create_buffer_with_data(data=np.ascontiguousarray(d.verts, np.float32).tobytes(),
                                                              usage=g.wgpu.BufferUsage.VERTEX)
            d.gpu["ubuf"] = g.device.create_buffer(size=UFLOATS * 4,
                                                   usage=g.wgpu.BufferUsage.UNIFORM | g.wgpu.BufferUsage.COPY_DST)
            d.gpu["views"] = {slot: f._tex(slot, d.material.textures.get(slot), d.material.srgb(slot)) for slot in SLOTS}
            if d.decal_rec is not None and getattr(self, "plate", None) is not None:
                d.gpu["views"][3] = self.plate             # the shot's scene light (decal plate mode, slot 3 unused)
        key = ("bind", id(zview))
        if key not in d.gpu:
            for k in [k for k in d.gpu if isinstance(k, tuple) and k[0] == "bind"]:
                del d.gpu[k]
            v = d.gpu["views"]
            d.gpu[key] = g.device.create_bind_group(layout=f.bgl, entries=[
                {"binding": 0, "resource": {"buffer": d.gpu["ubuf"], "offset": 0, "size": UFLOATS * 4}},
                {"binding": 1, "resource": f.s_rep}, {"binding": 2, "resource": f.s_clamp},
                *[{"binding": 3 + s, "resource": v[s]} for s in range(11)],
                {"binding": 14, "resource": zview}])
        return d.gpu[key]

    def _floor(self, gpu, enc, col, dep, M):
        """The depth-only ground plane: writes the scene depth (no colour) where it is nearer than what is there."""
        f = self.fxr
        y, half = self.floor_patch()
        u = np.zeros(20, np.float32)
        u[0:16] = M.T.ravel()
        u[16:20] = (0.0, y, 2.0 * half, 0.0)
        if "floor_ub" not in f.__dict__:
            f.floor_ub = gpu.device.create_buffer(size=80, usage=gpu.wgpu.BufferUsage.UNIFORM | gpu.wgpu.BufferUsage.COPY_DST)
            f.floor_bg = gpu.device.create_bind_group(layout=f.bgl_f, entries=[
                {"binding": 0, "resource": {"buffer": f.floor_ub, "offset": 0, "size": 80}}])
        gpu.device.queue.write_buffer(f.floor_ub, 0, u.tobytes())
        rp = enc.begin_render_pass(
            color_attachments=[{"view": col.create_view(), "load_op": "load", "store_op": "store"}],
            depth_stencil_attachment={"view": dep.create_view(), "depth_load_op": "load", "depth_store_op": "store"})
        rp.set_pipeline(f.p_floor)
        rp.set_bind_group(0, f.floor_bg)
        rp.draw(6)
        rp.end()

    def uniforms(self, d, M, size, drange, cam) -> np.ndarray:
        m = d.material
        u = np.zeros(UFLOATS, np.float32)
        u[0:16] = M.T.ravel()
        _r, _u, w = cam.basis()
        u[16:19] = w
        u[19] = max(drange[1] - drange[0], 1e-6)
        u[20:24] = (m.blend, 1.0 / m.soft if m.soft > 0 else 0.0, float(m.multiplicative), self.post.target_scale())
        mesh = d.kind == "mesh"
        u[24:28] = (m.on("POPCORN_BLEND"), m.on("POPCORN_BLEND_MASK"), 1.0, mesh and m.on("FRESNEL"))
        u[28:32] = (m.on("UV_DISTORTION"), m.on("ALPHA_REMAP"), m.on("GRADIENT_REMAP"), mesh and m.on("VERTEX_DISPLACEMENT"))
        u[32:36] = (float(bool(m.atlas) and m.atlas.get("blending", 0) == 1), float(m.default), float(self.post.bare_alpha),
                    float(d.kind in ("billboard", "ribbon")))
        u[36:40] = m.ops
        u[40:44] = m.inv + (float(bool(m.atlas and m.atlas.get("alpha_only"))),)     # w: AtlasAlphaOnly
        u[44:48] = m.fresnel
        u[48:52] = (float(m.physical), self.post.physical_scale(), float(m.default and m.blend == 0),
                    float(bool(m.atlas and m.atlas.get("flipbook"))))                         # w: Atlas.Flipbook
        u[52:56] = mipbias.sample_biases(m.general_mip_bias, m.draw_resolution, self.post.quality) + (float(m.draw_resolution),)
        u[56:60] = m.tint
        if d.decal is not None:
            u[60:76] = np.asarray(d.decal, float).T.ravel()
        u[76:92] = np.linalg.inv(M).T.ravel()
        u[92:96] = (size[0], size[1]) + (tuple(map(float, m.atlas["subdiv"])) if m.atlas else (1.0, 1.0))  # zw: SubDiv
        u[96:96 + MATS_FLOATS] = mats_uniforms(d, getattr(self, "light", None), self.post)
        u[256:260] = (m.color_variant, 0.0, 0.0, 0.0)  # xv.x ColorVariant.VariantContribution (cb4[7].x)
        if m.water:                                      # fs_water: the decal box slot and xv.yzw (water_uniforms)
            u[60:76], u[257:260] = m.water[:16], m.water[16:19]
            u[36:40], u[44:48] = water_time_uniforms(m.water, float(getattr(d, "time", 0.0)), 6 in m.textures)
            amb = water_image_light(getattr(self, "light", None), w)
            if amb is not None:                          # mats block: ambient (u[108:111]) and env_col (u[112:115])
                u[108:111], u[112:115] = amb
        if d.decal_rec is not None:                      # decals re-use the Uber switch slots (view/fx.wgsl decal_eval)
            from . import decal as dc
            u[24:48] = d.decal_rec.uniforms()
            u[38:40] = (self.post.game_exposure() * self.post.target_scale(), dc.GROUND_ROUGHNESS)
            u[40] = float(getattr(self, "plate", None) is not None)
            u[41:44] = getattr(self, "plate_irr", None) or PLATE_IRRADIANCE
        return u

    def encode(self, gpu, enc, col, res, dep, M, size, drange, cam):
        f = self.fxr
        w, h = size
        if self.floor is not None:
            self._floor(gpu, enc, col, dep, M)
        zt = f.ztex(w, h)
        zview = zt.create_view()
        zb = gpu.device.create_bind_group(layout=f.bgl_z, entries=[{"binding": 0, "resource": dep.create_view(aspect="depth-only")}])
        rp = enc.begin_render_pass(color_attachments=[{"view": zview, "load_op": "clear", "store_op": "store",
                                                       "clear_value": (1.0, 0, 0, 0)}])
        rp.set_pipeline(f.p_resolve)
        rp.set_bind_group(0, zb)
        rp.draw(3)
        rp.end()
        if not self.draws:
            return
        binds = []
        for d in self.draws:
            b = self._bind(d, zview)
            gpu.device.queue.write_buffer(d.gpu["ubuf"], 0, self.uniforms(d, M, size, drange, cam).tobytes())
            binds.append(b)
        bind_of = {id(d): b for d, b in zip(self.draws, binds)}
        f.front = fxcull.front_face(M)              # the game keeps faces whose right-hand normal faces the camera
        ds = None
        for aura, group in _aura.runs(self.draws):     # EnableAuraStencil draws test the units' stencil (fx/aurastencil.py)
            if aura and ds is None:
                if "aura" not in f.__dict__:
                    f.aura = _aura.AuraStencil(f)
                ds = f.aura.build(enc, dep, size, getattr(self, "units", []))
            att = {"view": (ds if aura else dep).create_view(), "depth_load_op": "load", "depth_store_op": "store"}
            if aura:
                att.update(stencil_load_op="load", stencil_store_op="store")
            rp = enc.begin_render_pass(
                color_attachments=[{"view": col.create_view(), "resolve_target": res.create_view(), "load_op": "load",
                                    "store_op": "store"}], depth_stencil_attachment=att)
            if aura:
                rp.set_stencil_reference(_aura.AURA_REF)
            for d in group:
                rp.set_pipeline(f.aura.pipe(d) if aura else f.pipe_for(d))
                rp.set_bind_group(0, bind_of[id(d)])
                if getattr(d, "decal_set", None) is not None:     # fx/decal_pass.py: records + texture arrays
                    rp.set_bind_group(1, d.decal_set.bind_group(self.floor_patch()[0] if self.floor is not None else 0.0))
                else:                                               # view/shadow.py: the shadow maps (fx.wgsl, patched)
                    rp.set_bind_group(1, gpu.shadow.bind)
                rp.set_vertex_buffer(0, d.gpu["vbuf"])
                rp.draw(len(d.verts))
            rp.end()


# ---- seams for fx/api.py (docs/hdfx-mcp.md "Seams") -------------------------------------------------------------------

def _state_points(state) -> np.ndarray:
    pts = []
    for b in getattr(state, "layers", {}).values():
        for name, v in getattr(b, "fields", {}).items():
            if name == "Position" or name.endswith("Position") or name.startswith(("Position",)):
                pts.append(np.asarray(v, float).reshape(-1, 3))
        for inp in getattr(b, "renderer_inputs", {}).values():
            if "Position" in inp:
                pts.append(np.asarray(inp["Position"], float).reshape(-1, 3))
    pts = [p[np.isfinite(p).all(1)] for p in pts]
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def camera_from_spec(spec: dict, size, state=None):
    """The renderer's camera for fx_render's `cam` dict: {"preset": "game"|"orbit", "az", "el", "k"|"dist", "target"}.
    Orthographic always (the game camera; "orbit" = the same projection at the given angles). Without a scale the view
    fits the first frame's particle bounds plus a margin (at least 6 world units across), so a clip keeps one camera."""
    from ..view import camera
    spec = dict(spec or {})
    el = float(spec.get("el", camera.GAME_EL))
    az = float(spec.get("az", camera.GAME_AZ))
    W, H = size
    if "k" in spec:
        k = float(spec["k"])
    elif spec.get("preset") == "game" and "dist" not in spec:
        k = camera.k_for_height(H)
    else:
        P = _state_points(state) if state is not None else np.zeros((0, 3))
        span = float(spec.get("dist", 0.0)) or (float(np.ptp(P, axis=0).max()) * 1.6 + 4.0 if len(P) else 12.0)
        k = min(W, H) / max(span, 6.0)
    cam = camera.OrthoCamera(el, az, k)
    cam.target = tuple(spec.get("target") or ((_state_points(state).mean(0).tolist()) if state is not None and
                                                  len(_state_points(state)) else (0.0, 0.0, 0.0)))
    return cam


def base_scene(gpu, vfs, unit=None, scene=None, clip=None, t=0.0, yaw=0.0, cam=None):
    """The opaque scene under an effect: a scene.Unit (or a unit path) posed with `clip` at `t`; map scenes are the
    context lane's (scene.base if it has one). Returns a gpu.Prepared or None."""
    from ..view import scene as S
    if scene is not None and getattr(scene, "base", None) is not None:
        return scene.base
    if unit is None:
        return None
    if isinstance(unit, str):
        unit = S.load_unit(vfs, unit)
    clip = clip or ("neutral" if "neutral" in getattr(unit, "clips", {}) else None)
    return gpu.prepare(unit, clip, t, yaw, cam, pose_kw={"loop": True})


# ---- input helpers

CHAIN = "\0chain"     # inputs_for key: the ParentID of each ribbon particle (one strip per parent)


def layer_batch(state, r):
    """The ParticleBatch of renderer `r`'s own layer. Layers can share a name (the cave mana shrine has two `Bubble`
    slots, 5 and 7; the Sim keys the second `Bubble#7`): the batch whose `renderer_inputs` holds `r.index` wins, so a
    renderer never draws another slot's particles (lane calib5: renderer 5 drew slot 5's bubbles with the default Size 1).
    Falls back to the name."""
    named = [b for k, b in state.layers.items() if k == r.layer or k.startswith(r.layer + "#")]
    for b in named:
        ri = getattr(b, "renderer_inputs", None) or {}
        if r.index in ri:
            return b
    return state.layers.get(r.layer)


def inputs_for(r, batch) -> dict:
    """A renderer's inputs {input name: (count, k) array} in a ParticleBatch: `batch.renderer_inputs[renderer index]`
    (lane vm fills it, docs/hdfx.md section 2), else each input's stream (rspec Input.field, the graph's field name) in
    `batch.fields`, else the input's constant from the file. Particles whose `Enabled` input is 0 are left out."""
    ri = getattr(batch, "renderer_inputs", None) or {}
    out = None
    for key in (r.index, getattr(r, "obj", None)):
        got = ri.get(key)
        if got:
            out = {k: np.asarray(v) for k, v in got.items()}
            break
    if out is None:
        fields = getattr(batch, "fields", {}) or {}
        n = int(getattr(batch, "count", 0))
        out = {}
        for name, inp in r.inputs.items():
            f = inp.field
            if isinstance(f, str) and f in fields and len(fields[f]) == n:
                out[name] = np.asarray(fields[f])
            elif inp.const is not None and n:
                out[name] = np.tile(np.asarray(inp.const, float).reshape(1, -1), (n, 1))
    out = {k: (v.astype(float).reshape(len(v), -1 if len(v) else (v.shape[1] if v.ndim > 1 else 1))
               if v.ndim >= 1 else v) for k, v in out.items()}             # (an empty batch keeps its width)
    par = (getattr(batch, "fields", {}) or {}).get("ParentID")
    if r.kind == "ribbon" and par is not None and out and len(par) and len(par) == _count(out, batch):
        out[CHAIN] = np.asarray(par, float).reshape(len(par), -1)[:, -1:]   # ribbons chain per parent (lane cast)
    if "Enabled" in out and len(out["Enabled"]):
        on = out["Enabled"][:, 0] != 0
        if not on.all():
            out = {k: (v[on] if np.ndim(v) >= 1 and len(v) == len(on) else v) for k, v in out.items()}
    return out


def _count(inputs, batch) -> int:
    for v in inputs.values():
        a = np.asarray(v)
        if a.ndim >= 1:
            return int(a.shape[0])
    return int(getattr(batch, "count", 0))


def _arr(inputs, names, n, k, batch=None, default=0.0):
    """(n, k) array of the first input in `names` (then the batch's field of that name); `default` None -> None."""
    for nm in names:
        v = inputs.get(nm)
        if v is None and batch is not None:
            v = getattr(batch, "fields", {}).get(nm)
        if v is not None:
            a = np.asarray(v, float).reshape(n, -1) if np.ndim(v) >= 1 and np.shape(v)[0] == n else np.tile(
                np.asarray(v, float).reshape(1, -1), (n, 1))
            if a.shape[1] == 1 and k > 1:
                a = np.repeat(a, k, 1)
            if a.shape[1] < k:
                a = np.hstack([a, np.zeros((n, k - a.shape[1]))])
            return a[:, :k]
    if default is None:
        return None
    return np.full((n, k), float(default))


def _find(inputs, words, n, k, default):
    """The first input whose name contains one of `words` (light inputs: names not read yet)."""
    words = (words,) if isinstance(words, str) else words
    for name in inputs:
        if any(w.lower() in name.lower() for w in words):
            return _arr(inputs, (name,), n, k)
    return np.tile(np.asarray(default, float).reshape(1, -1), (n, 1))[:, :k] if len(default) >= k else \
        np.hstack([np.tile(np.asarray(default, float), (n, 1)), np.ones((n, k - len(default)))])



# ======================================================================================================================
# Lane mats: the other D2R materials (docs/hdfx-shaders.md sections 8-12): Smoke, lit Uber, Liquid, default mesh,
# standard mesh, stencil. Kept in this section (lane polish edits the Uber code above).

LIQUID_SLOTS = {0: "Liquid.LiquidBase", 1: "Liquid.LiquidMask", 2: "Liquid.SpeckleMap", 3: "LiquidColorMap.ColorMap",
                6: "UVDistortion.BlendDistort", 8: "AlphaRemap.AlphaMap", 9: "GradientRemap.GradientMap",
                10: "Liquid.NormalMap"}
DMESH_SLOTS = {0: "Diffuse.DiffuseMap", 2: "Lit.RoughMetalMap", 4: "Fresnel.Gradient",
               5: "VertexDisplacement.DisplacementMap", 10: "Lit.NormalMap"}
STENCIL_SLOTS = {0: "AlphaThreshold.Mask", 8: "AlphaThreshold.AlphaRemap"}
MODEL_SLOTS = {"AlbedoTexture": 0, "ORMTexture": 2, "NormalTexture": 10}     # a model's own maps (Standard_Mesh)
NOT_DRAWN = ("Distortion_Billboard", "Default_Sound", "Default_Decal", "Default_Triangle")   # no D2R class
LIT_MODES = {"uber": 1, "smoke": 1, "liquid": 2, "dmesh": 3, "standard": 5}
MATS_FLOATS = 160      # lit .. npt (8 vec4) + 32 point-light vec4
MAX_FX_POINTS = 16


def _slots(get, table) -> dict:
    out = {}
    for slot, name in table.items():
        v = get(name)
        if isinstance(v, str) and v:
            out[slot] = v
    return out


def _f(v, default=0.0) -> float:
    v = _num(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def headlight(v) -> tuple:
    """Liquid.HeadlightContribution: a float2 whose bits the property reader returns as ints (1065353216 = 1.0)."""
    import struct as _st
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        if all(isinstance(x, int) for x in v[:2]):
            return tuple(_st.unpack("<2f", _st.pack("<2i", int(v[0]), int(v[1]))))
        return float(v[0]), float(v[1])
    return 1.0, 0.002


def mats_material(m: "Material", r) -> "Material":
    """Material.of's second half for every D2R material class (shad.material_family): the family, its keywords and
    textures, the lit mode, entry point and the liquid/stencil constants. Uber renderers keep Material.of's values
    (plus Smoke's flag +0x88 and the lit switches)."""
    from . import shad
    get = r.props.get
    fam = shad.material_family(r.material, r.kind)
    if fam is None and not any(k in (r.material or "") for k in NOT_DRAWN):
        fam = "uber"                               # an unknown name (synthetic effects): the Uber path, as before
    m.family = fam
    if fam is None or fam in ("light", "decal", "default"):
        return m
    on = lambda k: bool(get(k))                                       # noqa: E731
    m.wrap = _f(get("NormalWrap.WrapFactor")) if on("NormalWrap") else 0.0
    m.bend = _f(get("NormalBend.NormalBendingFactor")) if on("NormalBend") and r.kind != "mesh" else 0.0
    inputs = set(r.inputs) if r.inputs else None
    if fam in ("uber", "smoke"):
        m.default = False                          # Material.of's name test takes Diablo_Smoke for a Default material
        if fam == "smoke":                         # D2R sets the Uber flag +0x88 for Diablo_Smoke*: cb4[0].z = 1
            m.inv = (m.inv[0], m.inv[1], 1)
        if on("FastLit") or on("FullLit"):
            m.lit, m.entry = 1, "fs_lit"
            nm = get("Lit.NormalMap")
            if isinstance(nm, str) and nm:
                m.textures[10] = nm
        return m
    m.keywords = shad.material_keywords(get, r.kind, r.material, inputs)
    m.default = False
    m.multiplicative = False
    m.soft = _f(get("SoftParticles.SoftnessDistance")) if m.on("SOFT_PARTICLES") else 0.0
    if fam == "liquid":
        m.textures = _slots(get, LIQUID_SLOTS)
        md = get("UVDistortion.MaskDistort")              # the liquid VS/PS distort with the Mask* pair (lane calib5)
        if isinstance(md, str) and md:
            m.textures[6] = md
        m.lit, m.entry = 2, "fs_liquid"
        hl = headlight(get("Liquid.HeadlightContribution"))
        m.liquid = (int(_f(get("Liquid.LiquidBaseOp"))), int(_f(get("LiquidColorMap.ColorMapOp"))),
                    int(bool(_num(get("Liquid.LiquidBaseInvert")))), int(bool(_num(get("LiquidColorMap.ColorMapInvert")))),
                    int(m.on("LIQUID_COLOR_MAP")), int(bool(_num(get("AlphaRemap.Dissolve")))), hl[0], hl[1])
    elif fam in ("dmesh", "standard"):
        m.textures = _slots(get, DMESH_SLOTS) if fam == "dmesh" else {}
        m.textures.setdefault(2, "@orm")
        m.lit = 3 if (fam == "standard" or m.on("POPCORN_LIGHTING")) else 4
        if fam == "standard":
            m.lit = 5
        m.entry = "fs_dmesh"
        m.opaque = fam == "standard" or m.blend == 4      # GUESS for Standard_Mesh: the engine's model draw is opaque
        if m.opaque:
            m.sort_offset = -1e8                            # opaque draws first (after stencils)
    elif fam == "stencil":
        m.textures = _slots(get, STENCIL_SLOTS)
        m.entry = "fs_stencil"
        m.sort_offset = -1e9                                # before the effect's other draws
        m.liquid = (0, 0, int(bool(_num(get("AlphaThreshold.Invert")))), 0, 0, 0, 0.0, 0.0)
    return m


def standard_material(mat: "Material", mesh) -> "Material":
    """A Standard_Mesh draw takes its model's own maps (albedo, ORM, normal); other materials are returned as they are.
    A model whose material is a WaterMaterial (gr2 params "water") draws as water: `water_material`."""
    if mat.family != "standard":
        return mat
    w = (getattr(mesh, "params", None) or {}).get("water")
    if w:
        return water_material(mat, mesh, w)
    tex = dict(mat.textures)
    for usage, path in (getattr(mesh, "maps", None) or {}).items():
        if usage in MODEL_SLOTS and path:
            tex[MODEL_SLOTS[usage]] = path
    return replace(mat, textures=tex)


# The game draws a WaterMaterial model with its own water_p (ps_water_p.bin; calib5 s3 capture eid 7811, r3-calibfix r4
# eid 19928: cb4 = the material's values in its order). It writes lerp(scene colour, lit water, a) over what is behind
# it, with the colour, alpha and shore foam from the water's thickness over the scene depth (view/fx.wgsl fs_water, lane
# r4-water). Before lane r4-water the viewer drew a fixed WATER_DEPTH_MIX of shallow/deep (kept for callers).
WATER_DEPTH_MIX = 0.5


def srgb_lin(c):
    c = np.asarray(c, float)
    return np.where(c < 0.04045, c / 12.92, ((np.abs(c) + 0.055) / 1.055) ** 2.4)


def water_uniforms(w: dict, flow_map: bool = False) -> tuple:
    """The 23 floats of fs_water from gr2's "water" params: (shallow rgb a, deep rgb a, foam rgb, ShoreFoamDepth,
    TransmissionMax, Roughness, flow normal intensity, detail intensity, then the uv scales per world unit of the flow
    normal (0.04 x WaterFlowNormalTextureScale), the detail (that x WaterFlowNormalDetailTextureScale) and the foam
    mask (0.08 x ShoreFoamTextureScale)), as ps_water_p reads cb4. The colours are sRGB in the material (the PS decodes)."""
    g = lambda k, d: w.get(k, d)
    sa, da, fa = (srgb_lin(g(k, (0, 0, 0))[:3]) for k in ("ShallowAlbedo", "DeepAlbedo", "FoamAlbedo"))
    nb = 0.04 * float(g("WaterFlowNormalTextureScale", 1.0))
    # A custom map needs the rotation of its +X basis. The shader rotates the sampled vector.
    flow = water_flow_vector(w, (1.0, 0.5)) if flow_map else water_flow_vector(w)
    out = (*sa, g("ShallowAlpha", 1.0), *da, g("DeepAlpha", 1.0), *fa, g("ShoreFoamDepth", 0.0),
           max(float(g("WaterTransmissionDistanceMax", 0.1)), 1e-4), g("Roughness", 0.1),
           g("WaterFlowNormalIntensity", 1.0), g("WaterFlowNormalDetailIntensity", 0.0),
           nb, nb * float(g("WaterFlowNormalDetailTextureScale", 1.0)), 0.08 * float(g("ShoreFoamTextureScale", 1.0)),
           *flow, g("WaterFlowNormalSpeed", 0.0), g("ShoreFoamAnimationSpeed", 0.0))
    return tuple(float(x) for x in out)


# The flow (lane r4-glow, ps_water_p of capture 4268): the flow map t13 is default_WATER.texture (4x4, every texel
# (255, 127, 0), read through an sRGB view) on the shrine water (UseFlowTexture 0); its (2g - 1, 2r - 1) is turned by
# WaterFlowDirection, which the PS passes to sin/cos as it is (90 -> 90 radians), times WaterFlowIntensity. The foam mask
# scrolls along normalize(cb10[55].xz) = (-1, 0) in that capture (a scene constant; GUESS: the same in every scene).
WATER_FLOW_MAP = (1.0, 0.21223)            # default_WATER r, g after the sRGB decode (127 -> 0.212)
WATER_WIND = (-1.0, 0.0)


def water_flow_vector(w: dict, rg=WATER_FLOW_MAP) -> tuple:
    """World xz offset per flow phase of a WaterMaterial (the PS's r7.xy x FlowIntensity)."""
    fw, fz = 2.0 * rg[0] - 1.0, 2.0 * rg[1] - 1.0
    a, k = float(w.get("WaterFlowDirection", 0.0)), float(w.get("WaterFlowIntensity", 0.0))
    s, c = np.sin(a), np.cos(a)
    return ((fw * c - fz * s) * k, (fz * c + fw * s) * k)


def water_time_uniforms(water: tuple, t: float, flow_map: bool = False) -> tuple:
    """fs_water's flow slots (u.ops, u.fres) at time t: (flow x, flow z, t x WaterFlowNormalSpeed, 0) or (intensity x cos, intensity x sin, time phase, 1), and the foam
    mask's offset (wind x t x ShoreFoamAnimationSpeed). The game's clock is cb10[2].w; the viewer uses the effect age."""
    fx, fz, spd, fspd = (tuple(water[19:23]) + (0.0, 0.0, 0.0, 0.0))[:4]
    return (fx, fz, t * spd, float(flow_map)), (WATER_WIND[0] * t * fspd, WATER_WIND[1] * t * fspd, 0.0, 0.0)


def water_material(mat: "Material", mesh, w: dict) -> "Material":
    k = WATER_DEPTH_MIX
    sa, da = np.asarray(w.get("ShallowAlbedo", (0, 0, 0)), float), np.asarray(w.get("DeepAlbedo", (0, 0, 0)), float)
    rgb = (1 - k) * sa[:3] + k * da[:3]
    a = (1 - k) * float(w.get("ShallowAlpha", 1.0)) + k * float(w.get("DeepAlpha", 1.0))
    tex = dict(mat.textures)
    tex[0], tex[2] = "@white", "@orm_water"
    maps = getattr(mesh, "maps", None) or {}
    for slot, usage in ((10, "NormalTexture"), (1, "DetailNormalTexture"), (3, "FoamMaskTexture")):
        if maps.get(usage):
            tex[slot] = maps[usage]
    flow_map = maps.get("WaterFlowTexture") if w.get("UseFlowTexture") else None
    tex.pop(6, None)
    if flow_map:
        tex[6] = flow_map
    if not maps.get("FoamMaskTexture"):
        tex[3] = "@white"
    if not maps.get("DetailNormalTexture"):
        tex[1] = "@nrm"
    return replace(mat, textures=tex, tint=(float(rgb[0]), float(rgb[1]), float(rgb[2]), float(a)), opaque=False,
                   sort_offset=0.0, entry="fs_water", blend=3, water=water_uniforms(w, bool(flow_map)),
                   notes=list(mat.notes) + ["water model: water_p (thickness over the scene depth, foam, Fresnel blend)"])


def water_image_light(light, v):
    """ps_water_p's image light (as ps_models_p's) for an up-facing water surface, from a game rig's Light.game SH and
    iblIntensity, in Light.ambient's units (/ pi): diffuse max(SH(up), 0) and, for the specular cube at R = reflect(-v, up),
    max(SH(R), 0) (the SH stands in for the cube as in game_shade). (ambient, env_col), or None without Light.game SH."""
    g = getattr(light, "game", None) or {}
    if not g.get("sh"):
        return None
    from ..view import ibl as I
    sh, k = np.asarray(g["sh"], float), float((g.get("ibl") or {}).get("intensity", 1.0)) / math.pi
    v = np.asarray(v, float) / max(float(np.linalg.norm(v)), 1e-9)
    return tuple(k * np.maximum(I.irradiance(sh, np.asarray(d, float)), 0.0) for d in ((0.0, 1.0, 0.0), (-v[0], v[1], -v[2])))


def mats_uniforms(d: "FxDraw", light, post: "Post") -> np.ndarray:
    """The lane-mats block of the uniforms (view/fx.wgsl FxU from `lit` on): lit mode and wrap, the scene light,
    liquid/stencil constants, the scene's point lights near the draw (gpu.pick_points)."""
    from ..view import gpu as G
    m = d.material
    u = np.zeros(MATS_FLOATS, np.float32)
    u[0:4] = (m.lit, m.wrap, float(post.gain), float(m.opaque))
    L = light or G.Light()
    L = L.for_class("unit") if hasattr(L, "for_class") else L
    kd = np.asarray(L.key_dir, float)
    u[4:7] = kd / max(float(np.linalg.norm(kd)), 1e-9)
    u[7] = float(getattr(L, "spec_gain", 1.0))
    u[8:11] = L.key_col
    u[12:15] = L.ambient
    u[16:19] = L.env_col
    u[20:24] = m.liquid[0:4]
    u[24:28] = m.liquid[4:8]
    u[25] = max(u[25], float(getattr(m, "dissolve", False)))     # lq2.y: AlphaRemap.Dissolve for the Uber path too
    pts = []
    if len(d.verts) and getattr(L, "points", None):
        P = d.verts[:, 0:3]
        c = P.mean(0)
        pts = [p for p in G.pick_points(L.points, c, float(np.linalg.norm(P - c, axis=1).max()))
               if not p.get("directional")][:MAX_FX_POINTS]
    u[28] = len(pts)
    u[29] = float(getattr(L, "nrm_green", 1.0))
    for k, p in enumerate(pts):
        o = 32 + 8 * k
        u[o:o + 3] = p["pos"]
        u[o + 3] = p["radius"]
        u[o + 4:o + 7] = p["color"]
        u[o + 7] = p.get("atten", 1.0)
    return u


def _pipe_for(fxr: "FxRenderer", d: "FxDraw"):
    """The pipeline of a draw: FxRenderer._pipe for blended draws; opaque meshes (m 4, standard) write depth; stencil
    draws write the far depth only (no colour)."""
    m = d.material
    cull = (getattr(m, "cull", "none"), getattr(fxr, "front", "ccw"))
    if d.entry in ("fs_decals_mul", "fs_decals_add"):          # every decal at once (fx/decal_pass.py)
        from . import decal_pass
        return decal_pass.pipeline(fxr, d.entry)
    if d.entry in ("fs_decal_mul", "fs_decal_add"):            # dst x k, then + light (fx/decal.py)
        return fxr._pipe(d.entry, -1 if d.entry == "fs_decal_mul" else 0)
    if not (m.opaque or d.entry == "fs_stencil"):
        return fxr._pipe(d.entry, -1 if m.multiplicative else m.blend, cull)
    key = ("mats", d.entry, m.opaque) + cull
    if key in fxr._pipes:
        return fxr._pipes[key]
    A = fxr.gpu.wgpu.VertexFormat
    attrs, off = [], 0
    for loc, (_n, k) in enumerate(pk.LAYOUT):
        attrs.append({"format": {3: A.float32x3, 4: A.float32x4}[k], "offset": off * 4, "shader_location": loc})
        off += k
    vb = [{"array_stride": pk.FLOATS * 4, "step_mode": "vertex", "attributes": attrs}]
    stencil = d.entry == "fs_stencil"
    tgt = {"format": "rgba16float", "write_mask": 0 if stencil else 0xF}
    p = fxr.gpu.device.create_render_pipeline(
        layout=fxr.layout, vertex={"module": fxr.module, "entry_point": "vs_fx", "buffers": vb},
        primitive={"topology": "triangle-list", "cull_mode": cull[0], "front_face": cull[1]},
        depth_stencil={"format": "depth32float", "depth_write_enabled": True,
                       "depth_compare": "always" if stencil else "less"},
        multisample={"count": fxr.gpu.msaa},
        fragment={"module": fxr.module, "entry_point": d.entry, "targets": [tgt]})
    fxr._pipes[key] = p
    return p


FxRenderer.pipe_for = _pipe_for


# What this renderer draws, for fx/coverage.py (SUPPORTED_RENDER_*): renderer kinds, material files (D2R's classes with
# a viewer path) and switched-on features (coverage.features_of names). Left out on purpose: Distortion,
# sound/triangle/mesh-sampling renderers.
SUPPORTED_KINDS = {"billboard", "ribbon", "mesh", "light", "decal"}
_MAT = "vfx/materials/%s.pkma"
_PKFX = "vfx/Library/PopcornFXCore/Materials/%s.pkma"
SUPPORTED_MATERIALS = {_MAT % n for n in (
    "Diablo_Uber", "Diablo_Uber_Mesh", "Diablo_Uber_Ribbon", "Diablo_Smoke", "Diablo_Smoke_Mesh", "Diablo_Liquid",
    "Diablo_Liquid_Mesh", "Diablo_Liquid_Ribbon", "Diablo_Default_Mesh", "Diablo_Standard_Mesh", "Diablo_Stencil",
    "Diablo_Stencil_Mesh", "Diablo_Light", "Diablo_Decal")} | {_PKFX % n for n in ("Default_Billboard", "Default_Ribbon")}
SUPPORTED_FEATURES = {
    "GeometryBillboard", "GeometryRibbon", "GeometryMesh", "GeometryLight", "Transparent", "Transparency", "Opaque",
    "MonsterFade", "Exposure", "Exposure.PhysicalExposure", "Color", "Blend", "BlendMask", "BlendMask.Invert",
    "AlphaMask", "AlphaMask.Invert", "AlphaMask.AlphaOnly", "GradientRemap", "SoftParticles", "UVDistortion",
    "AlphaRemap", "AlphaRemap.Dissolve", "Atlas", "Fresnel", "Fresnel.ApplyToAlpha", "Fresnel.ApplyToColor",
    "Fresnel.DoubleSided", "VertexDisplacement", "Multiplicative", "ModelInstanceTracking", "CustomTextureU",
    "CorrectDeformation", "TextureUVs", "TextureUVs.FlipU", "TextureUVs.FlipV", "General.EnableSize2D",
    "FastLit", "FullLit", "Lit", "NormalWrap", "NormalBend", "Diffuse", "Liquid", "Liquid.LiquidBaseInvert",
    "LiquidColorMap", "AlphaThreshold", "Light", "LightAttenuation", "Decal", "Decal.ApplyToAlbedo",
    "EnableMipBiasing",                                # fx/mipbias.py (lane r3-mipbias)
    "AlphaMask.AtlasAlphaOnly", "Atlas.Flipbook",   # lane r3-atlasalpha (VS #352, PS #3746/#3647)
    "BackfaceCulling",                                  # lane r3-cull: fx/cull.py, the game's cull mode per material
    "ColorVariant",                                     # lane r3-colorvariant: fx/colorvariant.py
    _aura.FEATURE}                                      # lane r3-aurastencil / aura2: fx/aurastencil.py
# lane r3-animuv: skinned FX meshes (fx/animmesh.py), ribbon UV flags (packers.ribbon_uv); decal Emissive: lane r3-decal2's
# fs_decals_add with fx/emissive.py's byte encoding (fx/decal.pack)
SUPPORTED_FEATURES |= {"Animation", "TextureRepeat", "TextureUVs.RotateTexture", "Emissive"}
# lane r3-decal: the decal surface channels, the decal switches the game ignores, and kind 4 (not drawn by the game)
from . import decal as _decal  # noqa: E402
SUPPORTED_KINDS |= _decal.SUPPORTED_KINDS
SUPPORTED_FEATURES |= _decal.SUPPORTED_FEATURES
SUPPORTED_MATERIALS |= _decal.SUPPORTED_MATERIALS


# lane r3-shadows: shadow casters (view/shadow.py) and the features they cover
def _caster_threshold(r):
    from ..view.shadow import caster_threshold
    return caster_threshold(r)


SUPPORTED_FEATURES |= {"Shadows", "Lit.CastShadows"}
