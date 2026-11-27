// hdkit.view fx: D2R's PopcornFX effects (Diablo_Uber, Diablo_Uber_Mesh, Diablo_Uber_Ribbon, Default_Billboard, decals),
// drawn after gpu.Renderer's opaque pass into the same linear rgba16float target (premultiplied alpha), depth-tested
// against the scene, soft particles from the scene depth (resolved to r32float before this pass).
//
// The Uber maths follow docs/hdfx-shaders.md (lane shaders: D2R's ps/vs_popcorn_uber_* disassembled and D2R's material
// code): mesh PS section 3.3, billboard/ribbon PS section 4.2, VS 3.1/4.1. Differences, said plainly:
//  - no weighted blended OIT: AlphaBlend (m 3) and Premultiplied (m 2) are drawn sorted back to front with
//    "over" blending (the notes' viewer approximation); m 4 (a mesh normal pass) is not drawn;
//  - the light grid (POPCORN_LIGHTING) and the dissolve map are not ported yet;
//  - samplers as captured (calib r3): material maps wrap, anisotropic x16; the Fresnel gradient (and the remap maps)
//    clamp-to-edge x16; the *.Sample property's other sampler choices are not read. Mip bias as each game PS does it
//    (hdkit/fx/mipbias.py MAPS): pad0.x on the material maps, pad0.y on liquid/default-mesh normal maps, pad0.z (the
//    global bias) on the lit Uber normal map; UV distortion maps, gradient remap, colour/speckle/ORM maps unbiased.
// One vertex format for every renderer kind (hdkit/fx/packers.py): billboards and ribbons come expanded on the CPU, as in
// the game (notes 4.1); mesh instances are expanded too, each vertex carrying its particle's inputs.

struct FxU {
  mvp: mat4x4<f32>,
  cam_w: vec4<f32>,     // xyz towards the camera (orthographic), w: depth span dmax - dmin (world units per NDC z)
  feat: vec4<f32>,      // x blend mode m (0 add no alpha, 1 add, 2 premultiplied, 3 alpha blend), y 1/softness (0 off),
                        // z multiplicative (POPCORN_MULTIPLICATIVE), w colour scale (Post: gain / exposure hold)
  f0: vec4<f32>,        // keywords: x POPCORN_BLEND, y POPCORN_BLEND_MASK, z (alpha mask: always on), w FRESNEL
  f1: vec4<f32>,        // x UV_DISTORTION, y ALPHA_REMAP, z GRADIENT_REMAP, w VERTEX_DISPLACEMENT
  f2: vec4<f32>,        // x Atlas.Blending (1: every texture mixes both frames), y material: 0 Uber, 1 Default (rgba diffuse),
                        // z additive output writes luminance as alpha (bare effect renders), w is a billboard/ribbon (discards)
  ops: vec4<f32>,       // Blend.BlendOp, BlendMask.MaskOp, AlphaMask.MaskOp, AlphaMask.AlphaOnly   (cb4[1])
  inv: vec4<f32>,       // BlendMask.Invert, AlphaMask.Invert, material flag +0x88 (cb4[0].z, 0), w AlphaMask.AtlasAlphaOnly (cb4[4].y)
  fres: vec4<f32>,      // Fresnel.BlendOp, ApplyToColor, ApplyToAlpha, DoubleSided   (cb4[3])
  exq: vec4<f32>,       // x Exposure.PhysicalExposure, y physical scale (1/pi x cb10[0].x), z Default: alpha from red,
                        // w Atlas.Flipbook (cb4[7].y: atlas uv used as is, no UVScaleAndOffset)
  pad0: vec4<f32>,      // mip bias (fx/mipbias.py): x material maps cb10[13].y + cb4[6].y, y normal maps cb10[13].y - 0.5,
                        // z the global cb10[13].y, w General.DrawResolution
  tint: vec4<f32>,      // colour multiplier (viewer control)
  dec: mat4x4<f32>,     // decal: world -> decal box space ([-0.5, 0.5]^3, y the projection axis)
  inv_mvp: mat4x4<f32>, // decal: clip -> world
  vp: vec4<f32>,        // xy: target size in pixels, zw: Atlas.SubDiv (cb4[4].zw)
  // ---- lane mats (docs/hdfx-shaders.md 8-12): lit materials, liquid, default mesh, stencil
  lit: vec4<f32>,       // x mode (0 unlit Uber, 1 lit Uber/Smoke, 2 liquid, 3 default mesh lit, 4 default mesh unlit,
                        //   5 standard mesh), y NormalWrap.WrapFactor, z scale of scene-lit colour (gain), w opaque (m 4)
  key_dir: vec4<f32>,   // scene light (gpu.Light): xyz towards the key light, w specular gain
  key_col: vec4<f32>,
  ambient: vec4<f32>,
  env_col: vec4<f32>,
  lq: vec4<f32>,        // liquid: LiquidBaseOp, LiquidColorMap.ColorMapOp, LiquidBaseInvert, ColorMapInvert (cb4[1].xy, cb4[2].xy)
  lq2: vec4<f32>,       // x LIQUID_COLOR_MAP, y AlphaRemap.Dissolve, z, w Liquid.HeadlightContribution (diffuse, specular)
  npt: vec4<f32>,       // x number of point lights, y normal-map green sign
  pts: array<vec4<f32>, 32>,   // point lights: [2k] xyz position, w radius; [2k+1] rgb colour x power, w attenuation
  // ---- float 256 (integration r3)
  xv: vec4<f32>,        // x ColorVariant.VariantContribution (cb4[7].x; 0: no COLOR_VARIANT); fs_water (lane r4-water):
                        // yzw uv scales per world unit of the flow normal map, the detail normal map, the foam mask
};

@group(0) @binding(0) var<uniform> u: FxU;
@group(0) @binding(1) var s_rep: sampler;
@group(0) @binding(2) var s_clamp: sampler;
@group(0) @binding(3) var t0: texture_2d<f32>;    // Blend.TextureBase / Diffuse.DiffuseMap / Decal.DiffuseMap
@group(0) @binding(4) var t1: texture_2d<f32>;    // Blend.TextureBlend
@group(0) @binding(5) var t2: texture_2d<f32>;    // BlendMask.Mask
@group(0) @binding(6) var t3: texture_2d<f32>;    // AlphaMask.Mask
@group(0) @binding(7) var t4: texture_2d<f32>;    // Fresnel.Gradient (sRGB view: decoded to linear)
@group(0) @binding(8) var t5: texture_2d<f32>;    // VertexDisplacement.DisplacementMap
@group(0) @binding(9) var t6: texture_2d<f32>;    // UVDistortion.BlendDistort
@group(0) @binding(10) var t7: texture_2d<f32>;   // UVDistortion.MaskDistort
@group(0) @binding(11) var t8: texture_2d<f32>;   // AlphaRemap.AlphaMap
@group(0) @binding(12) var t9: texture_2d<f32>;   // GradientRemap.GradientMap (sRGB view)
@group(0) @binding(13) var t10: texture_2d<f32>;  // normal map (DXT5nm) of the lit materials: Lit/Liquid.NormalMap
@group(0) @binding(14) var t_depth: texture_2d<f32>;   // scene depth (NDC z, r32float, one sample)

struct VIn {
  @location(0) pos: vec3<f32>,
  @location(1) nrm: vec3<f32>,
  @location(2) uv: vec4<f32>,       // xy: uv (frame A), zw: uv of atlas frame B
  @location(3) color: vec4<f32>,    // Color.Color: rgb sRGB (converted here), a
  @location(4) misc: vec4<f32>,     // x atlas blend (fract TextureID), y packed: atlas cell + 2048 (AtlasAlphaOnly) + 4096 x
                                    // the colour variant exponent x 256 (fx/colorvariant.pack_misc_y), z FresnelCursor,
                                    // w MonsterFade.Fade
  @location(5) so_base: vec4<f32>,  // UVScaleAndOffset (scale xy, offset zw) of base, blend, blend mask, alpha mask,
  @location(6) so_blend: vec4<f32>, // displacement
  @location(7) so_mask: vec4<f32>,
  @location(8) so_alpha: vec4<f32>,
  @location(9) so_disp: vec4<f32>,
  @location(10) sc: vec4<f32>,      // x BaseContribution, y BlendContribution, z ExposureMultiplier, w DisplacementDistance
  @location(11) dist: vec4<f32>,    // x BlendDistortIntensity, y MaskDistortIntensity, z colour variant tint (packed
                                    // 8:8:8, fx/colorvariant.py), w PhysicalIntensity
  @location(12) so_db: vec4<f32>,   // UVScaleAndOffset of the blend distortion and the mask distortion
  @location(13) so_dm: vec4<f32>,
  @location(14) cur: vec4<f32>,     // AlphaRemap.AlphaCursor xy, GradientRemap.GradientCursor xy
  @location(15) so_x: vec4<f32>,    // AlphaRemap.DissolveUVScaleAndOffset
};

struct VOut {
  @builtin(position) clip: vec4<f32>,
  @location(0) wpos: vec3<f32>,
  @location(1) nrm: vec3<f32>,
  @location(2) uv: vec4<f32>,
  @location(3) color: vec4<f32>,    // linear rgb, alpha x MonsterFade
  @location(4) misc: vec4<f32>,     // x atlas blend, y exposure scalar, z FresnelCursor, w atlas cell origin u
  @location(5) so_base: vec4<f32>,
  @location(6) so_blend: vec4<f32>,
  @location(7) so_mask: vec4<f32>,
  @location(8) so_alpha: vec4<f32>,
  @location(9) sc: vec4<f32>,
  @location(10) dist: vec4<f32>,    // z: atlas cell origin v (AtlasAlphaOnly)
  @location(11) so_db: vec4<f32>,
  @location(12) so_dm: vec4<f32>,
  @location(13) cur: vec4<f32>,
  @location(14) so_x: vec4<f32>,
  @location(15) @interpolate(flat) cv: vec4<f32>,   // colour variant: x exponent (0: none), y packed tint (8:8:8)
};

fn uvso(uv: vec2<f32>, so: vec4<f32>) -> vec2<f32> { return uv * so.xy + so.zw; }

// the material textures' uv: x UVScaleAndOffset, or as is with Atlas.Flipbook (PS #3746/#3647: movc on cb4[7].y)
fn uvsf(uv: vec2<f32>, so: vec4<f32>) -> vec2<f32> { return select(uvso(uv, so), uv, u.exq.w > 0.5); }

// AlphaMask.AtlasAlphaOnly: origin of atlas cell g (packers.atlas_cell), as the game's atlas VS #352 computes it:
// column g mod cols, row floor(g / rows) (packers.atlas_cell_origin)
fn cell_origin(g: f32, sd: vec2<f32>) -> vec2<f32> {
  let c = select(-sd.x, sd.x, g * sd.x >= 0.0);
  let col = c * fract(g / c);
  return vec2<f32>(col, floor(g / sd.y)) / sd;
}

fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
  return select(pow((c + vec3<f32>(0.055)) / 1.055, vec3<f32>(2.4)), c / 12.92, c <= vec3<f32>(0.04045));
}

// D2R's material ops (notes section 2): 0 a*b, 1 a+b, 2 min(a, max(a-b, 0)), 3 a + a*b
fn op(o: i32, a: f32, b: f32) -> f32 {
  if (o == 0) { return a * b; }
  if (o == 1) { return a + b; }
  if (o == 2) { return min(a, max(a - b, 0.0)); }
  return a + a * b;
}

// Fresnel.BlendOp: 0 a*b, 1 a+b, 2 a-b (no clamp), 3 a*b + a
fn fop(o: i32, a: vec4<f32>, b: vec4<f32>) -> vec4<f32> {
  if (o == 0) { return a * b; }
  if (o == 1) { return a + b; }
  if (o == 2) { return a - b; }
  return a * b + a;
}

@vertex
fn vs_fx(v: VIn) -> VOut {
  var o: VOut;
  var p = v.pos;
  if (u.f1.w > 0.5) {
    // VertexDisplacement (VS 3.1 step 1): map.r * 2 - 1 along the normal, x DisplacementDistance
    let d = textureSampleLevel(t5, s_rep, uvso(v.uv.xy, v.so_disp), 0.0).r * 2.0 - 1.0;
    p = p + normalize(v.nrm + vec3<f32>(1e-6)) * d * v.sc.w;
  }
  o.clip = u.mvp * vec4<f32>(p, 1.0);
  o.wpos = p;
  o.nrm = v.nrm;
  o.uv = v.uv;
  o.color = vec4<f32>(srgb_to_linear(max(v.color.rgb, vec3<f32>(0.0))), v.color.a * v.misc.w);
  // exposure scalar (VS 3.1 step 5): ExposureMultiplier x (PhysicalExposure ? PhysicalIntensity / pi x cb10[0].x : 1)
  var e = v.sc.z;
  if (u.exq.x > 0.5) { e = e * v.dist.w * u.exq.y; }
  o.misc = vec4<f32>(v.misc.x, e, v.misc.z, 0.0);
  o.dist = v.dist;
  // misc.y = (atlas cell + 2048) + 4096 x round(colour variant exponent x 256) (packers.MISC_Y_*, colorvariant.pack_misc_y);
  // dist.z = the variant's packed tint
  let cvq = floor(v.misc.y / 4096.0);
  o.cv = vec4<f32>(cvq / 256.0, v.dist.z, 0.0, 0.0);
  if (u.inv.w > 0.5) {
    let org = cell_origin(v.misc.y - cvq * 4096.0 - 2048.0, u.vp.zw);
    o.misc.w = org.x;
    o.dist.z = org.y;
  }
  o.so_base = v.so_base;
  o.so_blend = v.so_blend;
  o.so_mask = v.so_mask;
  o.so_alpha = v.so_alpha;
  o.sc = v.sc;
  o.so_db = v.so_db;
  o.so_dm = v.so_dm;
  o.cur = v.cur;
  o.so_x = v.so_x;
  return o;
}

// COLOR_VARIANT (fx/colorvariant.py; PS: e = table[TransformIndex - 8]): v = c ^ e.adjust.w, v = lerp(v, v x tint.rgb,
// tint.a) (folded into the packed tint), c = lerp(c, v, VariantContribution). Exponent 0 or contribution 0: unchanged.
fn color_variant(c: vec3<f32>, i: VOut) -> vec3<f32> {
  if (i.cv.x <= 0.0 || u.xv.x == 0.0) { return c; }
  let q = u32(i.cv.y + 0.5);
  let t = vec3<f32>(f32(q & 255u), f32((q >> 8u) & 255u), f32(q >> 16u)) / 255.0;
  let v = pow(abs(c), vec3<f32>(i.cv.x)) * t;
  return mix(c, v, u.xv.x);
}

// one texture at frame A, or mixed with frame B (Atlas.Blending 1: every texture is sampled for both frames)
fn samp2(t: texture_2d<f32>, uva: vec2<f32>, uvb: vec2<f32>, fb: f32) -> vec4<f32> {
  let a = textureSampleBias(t, s_rep, uva, u.pad0.x);
  let b = textureSampleBias(t, s_rep, uvb, u.pad0.x);
  return select(a, mix(a, b, fb), u.f2.x > 0.5);
}

fn soft_factor(clip: vec4<f32>) -> f32 {
  if (u.feat.y <= 0.0) { return 1.0; }
  let zs = textureLoad(t_depth, vec2<i32>(clip.xy), 0).r;
  if (zs >= 0.99999) { return 1.0; }          // nothing drawn behind (cleared depth): no surface to fade against
  let gap = (zs - clip.z) * u.cam_w.w;        // world units from the fragment to the scene behind it
  return clamp(gap * u.feat.y, 0.0, 1.0);
}

// The Uber colour (linear rgb, alpha) of a fragment before the output rule (PS 3.3 steps 1-7, 4.2).
fn uber(i: VOut) -> vec4<f32> {
  // 0. atlas (UV_MODE 2): i.uv = the atlas cell uv of frames A (xy) and B (zw). AlphaMask.AtlasAlphaOnly (VS #352):
  // only the alpha mask keeps them; every other texture gets the cell-local sat((uv - origin of A's cell) x SubDiv)
  // (per fragment here, per vertex in the game: the same, the cell offsets being whole cells)
  let uvA = i.uv.xy;
  let uvA2 = i.uv.zw;
  var uv = uvA;
  var uv2 = uvA2;
  if (u.inv.w > 0.5) {
    let org = vec2<f32>(i.misc.w, i.dist.z);
    uv = clamp((uvA - org) * u.vp.zw, vec2<f32>(0.0), vec2<f32>(1.0));
    uv2 = clamp((uvA2 - org) * u.vp.zw, vec2<f32>(0.0), vec2<f32>(1.0));
  }
  let fb = i.misc.x;
  // 1. UV distortion: base/blend at uvB, blend mask at uvM, alpha mask at uvAM (= uvM unless AtlasAlphaOnly), clamped
  // to [0, 0.99]
  var uvB = uv;
  var uvM = uv;
  var uvAM = uvA;
  var uvB2 = uv2;
  var uvM2 = uv2;
  var uvAM2 = uvA2;
  let db = textureSample(t6, s_rep, uvsf(uv, i.so_db)).rg * 2.0 - vec2<f32>(1.0);     // game: t14/t15 unbiased
  let dm = textureSample(t7, s_rep, uvsf(uv, i.so_dm)).rg * 2.0 - vec2<f32>(1.0);
  if (u.f1.x > 0.5) {
    uvB = clamp(uv + db * i.dist.x, vec2<f32>(0.0), vec2<f32>(0.99));
    uvM = clamp(uv + dm * i.dist.y, vec2<f32>(0.0), vec2<f32>(0.99));
    uvAM = clamp(uvA + dm * i.dist.y, vec2<f32>(0.0), vec2<f32>(0.99));
    // frame B (Atlas.Blending 1) is not distorted, only clamped (PS #3647: mov r2.zw, v1.zw before the clamp)
    uvB2 = clamp(uv2, vec2<f32>(0.0), vec2<f32>(0.99));
    uvM2 = uvB2;
    uvAM2 = clamp(uvA2, vec2<f32>(0.0), vec2<f32>(0.99));
  }
  let base = samp2(t0, uvsf(uvB, i.so_base), uvsf(uvB2, i.so_base), fb);
  let blend = samp2(t1, uvsf(uvB, i.so_blend), uvsf(uvB2, i.so_blend), fb).r;
  var bm = samp2(t2, uvsf(uvM, i.so_mask), uvsf(uvM2, i.so_mask), fb).r;
  var am = samp2(t3, uvsf(select(uvM, uvAM, u.inv.w > 0.5), i.so_alpha), uvsf(select(uvM2, uvAM2, u.inv.w > 0.5), i.so_alpha), fb).r;
  let nv = dot(normalize(i.nrm + vec3<f32>(1e-6)), normalize(u.cam_w.xyz));
  var c: vec4<f32>;
  if (u.f2.y > 0.5) {
    // PopcornFX Default_Billboard (notes 5, DRAW_MODE 1): diffuse x colour; alpha = texture alpha (red when m == 0)
    c = base;
    if (u.exq.z > 0.5) { c.a = base.r; }
  } else {
    // 2. Blend: grey g = op(Base.r x BaseContribution, Blend.r x BlendContribution)
    var g = base.r * i.sc.x;
    if (u.f0.x > 0.5) { g = op(i32(u.ops.x + 0.5), g, blend * i.sc.y); }
    // 3. masks
    if (u.inv.x > 0.5) { bm = 1.0 - bm; }
    if (u.inv.y > 0.5) { am = 1.0 - am; }
    var rgb = vec3<f32>(g);
    var a: f32;
    if (u.inv.z > 0.5) {
      a = op(i32(u.ops.z + 0.5), bm, am);
    } else {
      if (u.f0.y > 0.5) { rgb = vec3<f32>(op(i32(u.ops.y + 0.5), g, bm)); }
      a = select(op(i32(u.ops.z + 0.5), rgb.r, am), am, u.ops.w > 0.5);
    }
    c = vec4<f32>(rgb, a);
  }
  // 4. alpha remap (lane r3-shapes, mesh PS 1375 / billboard PS 3647, cb4[6].x = AlphaRemap.Dissolve): Dissolve:
  // f = sat(Dissolve.r(uvM x DissolveUVScaleAndOffset) - cursor.x), grey and alpha each sat(x f); else
  // alpha = AlphaMap(alpha + cursor.x, cursor.y). (The dissolve map wraps here: its offsets scroll past 1; GUESS.)
  if (u.f1.y > 0.5) {
    if (u.lq2.y > 0.5) {
      let f = clamp(samp2(t8, uvso(uvM, i.so_x), uvso(uvM2, i.so_x), fb).r - i.cur.x, 0.0, 1.0);
      c = vec4<f32>(vec3<f32>(clamp(c.r * f, 0.0, 1.0)), clamp(c.a * f, 0.0, 1.0));
    } else {
      c.a = textureSampleBias(t8, s_clamp, vec2<f32>(c.a + i.cur.x, i.cur.y), u.pad0.x).r;
    }
  }
  // 5. Fresnel: G = Gradient(1 - f, FresnelCursor) (linear), op per channel, applied to colour and/or alpha
  if (u.f0.w > 0.5) {
    var f = nv;
    if (u.fres.w > 0.5) { f = abs(f); }
    let G = textureSampleBias(t4, s_clamp, vec2<f32>(1.0 - f, i.misc.z), u.pad0.x);
    let r = fop(i32(u.fres.x + 0.5), c, G);
    if (u.fres.y > 0.5) { c = vec4<f32>(r.rgb, c.a); }
    if (u.fres.z > 0.5) { c.a = r.a; }
    c = max(c, vec4<f32>(0.0));
  }
  // 6. gradient remap: GradientMap(c.r + cursor.x, cursor.y).rgb (linear)
  if (u.f1.z > 0.5) {
    c = vec4<f32>(textureSample(t9, s_clamp, vec2<f32>(c.r + i.cur.z, i.cur.w)).rgb, c.a);   // game: t17 unbiased
  }
  // 7. x Color (linear, alpha x MonsterFade), the colour variant, rgb x exposure (and the post's colour scale)
  c = c * i.color * u.tint;
  c = vec4<f32>(color_variant(c.rgb, i), c.a);
  return vec4<f32>(c.rgb * i.misc.y * u.feat.w, c.a);
}

// 10. output: m 0 rgb, m 1 rgb x alpha (additive, alpha 0); m 3 alpha blend, m 2 premultiplied ("over", sorted);
// multiplicative: dst x mix(1, rgb, alpha)
fn finish(c: vec4<f32>) -> vec4<f32> {
  let m = i32(u.feat.x + 0.5);
  let a = clamp(c.a, 0.0, 1.0);
  if (u.feat.z > 0.5) { return vec4<f32>(mix(vec3<f32>(1.0), c.rgb, a), 1.0); }
  if (m <= 1) {
    let add = select(c.rgb, c.rgb * c.a, m == 1);
    return vec4<f32>(add, select(0.0, clamp(max(add.r, max(add.g, add.b)), 0.0, 1.0), u.f2.z > 0.5));
  }
  if (m == 3) { return vec4<f32>(c.rgb * a, a); }
  return vec4<f32>(c.rgb, a);
}

@fragment
fn fs_fx(i: VOut) -> @location(0) vec4<f32> {
  var c = uber(i);
  if (u.f2.w > 0.5 && (i.color.a < 1.0 / 1023.0 || c.a < 1.0 / 1023.0)) { discard; }   // billboard early discards
  c.a = c.a * soft_factor(i.clip);                                                       // 8. soft particles
  let o = finish(c);
  if (o.a <= 0.0 && max(o.r, max(o.g, o.b)) <= 0.0) { discard; }
  return o;
}

// Decals (lane r3-decal; hdkit/fx/decal.py has the game's maths and a CPU reference): D2R blends the decal into the
// surface MATERIAL (albedo, normal, occlusion, roughness, metalness, by the weight) inside ps_terrain, then lights it.
// The viewer keeps no G-buffer, so each decal relights what is already drawn: with D = the diffuse light per unit albedo
// and S = the specular + environment light of a surface, ground = A D_g + S_g (A unknown) and the decal surface
// = A' D' + S' with A' = mix(A, decal albedo, w_a): out = dst k + add, k = (1 - w_a) D' / D_g,
// add = w_a albedo D' + S' - k S_g + emissive (fs_decal_mul writes k with dst x src blending, fs_decal_add the rest).
// Decal uniforms re-use the Uber switch slots: f0 = box X (world) | flags, f1 = box Y (projection axis) | edge fade,
// f2 = cos min, cos max, threshold, threshold sharpness (hard^2 x 64), ops = emissive intensity, emissive power,
// emissive scale (game exposure x target scale), ground roughness; inv.x 1 = plate mode: a game shot is added after this
// pass (render.hdr_over_bg), so t3 holds its scene light and fs_decal_add writes plate x (k - 1) + add (no mul pass);
// the shot's ground is A x E_plate (inv.yzw, render.PLATE_IRRADIANCE), so the decal's light is scaled by E_plate / D_g.
struct DecalOut { ok: bool, k: vec3<f32>, add: vec3<f32>, };

fn decal_eval(i: VOut) -> DecalOut {
  var o: DecalOut;
  o.ok = false;
  let px = vec2<i32>(i.clip.xy);
  let zs = textureLoad(t_depth, px, 0).r;
  let ndc = vec2<f32>(i.clip.x / u.vp.x * 2.0 - 1.0, 1.0 - i.clip.y / u.vp.y * 2.0);
  let wh = u.inv_mvp * vec4<f32>(ndc, zs, 1.0);
  let wp = wh.xyz / wh.w;
  let lp = (u.dec * vec4<f32>(wp, 1.0)).xyz;
  let uv = vec2<f32>(lp.x + 0.5, lp.z + 0.5);
  // derivatives before any discard (uniform control flow): texture gradients and the surface normal from the depth
  let gx = dpdx(uv);
  let gy = dpdy(uv);
  var ng = cross(dpdy(wp), dpdx(wp));
  let v = normalize(u.cam_w.xyz);
  if (dot(ng, ng) < 1e-20) { ng = vec3<f32>(0.0, 1.0, 0.0); }
  ng = normalize(ng);
  if (dot(ng, v) < 0.0) { ng = -ng; }
  let alb = textureSampleGrad(t0, s_clamp, uv, gx, gy);
  let orm = textureSampleGrad(t1, s_clamp, uv, gx, gy);
  let em = textureSampleGrad(t2, s_clamp, uv, gx, gy).r;
  let nm = textureSampleGrad(t10, s_clamp, uv, gx, gy);
  if (zs >= 1.0 || any(abs(lp) > vec3<f32>(0.5))) { return o; }
  let fl = u32(u.f0.w + 0.5);
  let xa = u.f0.xyz;
  let ya = u.f1.xyz;
  // weight: albedo alpha x xz edge fade x angle cut x threshold
  var w = alb.a;
  let e = u.f1.w;
  if (e > 0.0) {
    let f = vec2<f32>(1.0) - clamp((abs(lp.xz) - vec2<f32>(0.5 * (1.0 - e))) / (0.5 * e), vec2<f32>(0.0), vec2<f32>(1.0));
    w = w * f.x * f.y;
  }
  if ((fl & 1u) != 0u) {
    let c = dot(ng, ya);
    w = w * select(0.0, 1.0, u.f2.x < c && c < u.f2.y);
  }
  if ((fl & 64u) != 0u) { w = w * clamp((orm.a - u.f2.z) * u.f2.w + 0.5, 0.0, 1.0); }
  if (w <= 0.0) { return o; }
  // the material: ground (A unknown, ao 1, roughness ops.w, metal 0, normal ng) -> decal
  var n = ng;
  if ((fl & 4u) != 0u) {
    let x = nm.a * 2.0 - 1.0;
    let y = nm.g * 2.0 - 1.0;
    let z = sqrt(1.0 - min(x * x + y * y, 1.0));
    let d = normalize(x * xa + y * cross(xa, ya) + z * ya);
    n = normalize(mix(ng, d, w));
  }
  let rg = u.ops.w;
  let ao = select(1.0, mix(1.0, orm.r, w), (fl & 8u) != 0u);
  let rough = max(select(rg, mix(rg, orm.g, w), (fl & 16u) != 0u), 0.04);
  let metal = select(0.0, mix(0.0, orm.b, w), (fl & 32u) != 0u);
  let wa = select(0.0, w, (fl & 2u) != 0u);
  let s1 = light_scene(vec3<f32>(0.0), n, v, wp, rough, metal, ao);
  let d1 = light_scene(vec3<f32>(1.0), n, v, wp, rough, metal, ao) - s1;
  let sg = light_scene(vec3<f32>(0.0), ng, v, wp, rg, 0.0, 1.0);
  let dg = light_scene(vec3<f32>(1.0), ng, v, wp, rg, 0.0, 1.0) - sg;
  o.k = (1.0 - wa) * d1 / max(dg, vec3<f32>(1e-6));
  var sc = vec3<f32>(1.0);
  if (u.inv.x > 0.5) { sc = u.inv.yzw / max(dg, vec3<f32>(1e-6)); }
  var add = (wa * alb.rgb * d1 + s1 - o.k * sg) * sc * u.lit.z;
  if ((fl & 128u) != 0u) {
    // E = albedo' x w x intensity x mask x max(N.V, 0.001)^power x (N.V + 1) / pi, in game units x exposure
    let nv = dot(n, v);
    let ek = u.ops.x * em * pow(max(nv, 0.001), u.ops.y) * (nv + 1.0) * PI_INV;
    add = add + alb.rgb * wa * w * ek * u.ops.z;
  }
  if (u.inv.x > 0.5) {
    let pl = textureSampleLevel(t3, s_clamp, i.clip.xy / u.vp.xy, 0.0).rgb;
    add = add + pl * (o.k - vec3<f32>(1.0));
  }
  o.add = add;
  o.ok = true;
  return o;
}

@fragment
fn fs_decal_mul(i: VOut) -> @location(0) vec4<f32> {
  let o = decal_eval(i);
  if (!o.ok || u.inv.x > 0.5) { discard; }
  return vec4<f32>(o.k, 1.0);
}

@fragment
fn fs_decal_add(i: VOut) -> @location(0) vec4<f32> {
  let o = decal_eval(i);
  if (!o.ok) { discard; }
  return vec4<f32>(o.add, 0.0);
}


// =====================================================================================================================
// Lane mats: the other D2R materials (docs/hdfx-shaders.md sections 8-12). Kept apart from the Uber code above.
// Scene lighting as the game's lit PopcornFX shaders do it (GGX key light, F0 0.04, wrapped diffuse N.L = wrap + (1 -
// wrap) sat(N.L), local lights, ambient), in the viewer's light units (view/shaders.wgsl: the SH ambient and the IBL
// cube are the viewer's ambient and environment colours), so lit effects match the lit units around them.

const PI_INV: f32 = 0.31830988;

fn d_ggx(n: vec3<f32>, h: vec3<f32>, rough: f32) -> f32 {
  let a = rough * rough;
  let nh = max(dot(n, h), 0.0);
  let d = nh * nh * (a * a - 1.0) + 1.0;
  return a * a / max(3.14159265 * d * d, 1e-6);
}

// GGX specular x N.L of one light (the game's: D x Fresnel-Schlick (F0) x a Smith visibility)
fn spec_ggx(n: vec3<f32>, v: vec3<f32>, l: vec3<f32>, rough: f32, f0: vec3<f32>) -> vec3<f32> {
  let h = normalize(v + l);
  let nl = max(dot(n, l), 0.0);
  let nv = abs(dot(n, v)) + 1e-3;
  let a = rough * rough;
  let vis = 0.5 / max(nl * (nv * (1.0 - a) + a) + nv * (nl * (1.0 - a) + a), 1e-5);
  let f = f0 + (vec3<f32>(1.0) - f0) * pow(1.0 - max(dot(h, v), 0.0), 5.0);
  return d_ggx(n, h, rough) * vis * f * nl;
}

// Point-light distance term: w >= 0 the map-light form (1 - (d / r)^2)^w; w < 0 D2R's own (effect lights, calib3):
// max(1 - (d / r)^-w, 0)^2 / (1 + 0.0929 d^2) (hdkit.fx.packers.LIGHT_INV_SQ).
fn point_falloff(d: f32, r: f32, w: f32) -> f32 {
  if (w < 0.0) {
    let g = max(1.0 - pow(min(d / max(r, 1e-6), 1.0), -w), 0.0);
    return g * g / (1.0 + 0.092903040349 * d * d);
  }
  let q = clamp(1.0 - (d * d) / max(r * r, 1e-6), 0.0, 1.0);
  return pow(q, w);
}

// Shadow maps (hdkit/view/shadow.py) on Standard meshes only (the engine's model shader; lit particles read none). The CLI
// swaps the stub block for the real maps (bind group 1).
// @shadow-stub-begin
fn shd_global(wpos: vec3<f32>, n: vec3<f32>) -> f32 { return 1.0; }
fn shd_light(j: i32, wpos: vec3<f32>, n: vec3<f32>, g: f32) -> f32 { return 1.0; }
fn shd_local(lpos: vec3<f32>, wpos: vec3<f32>, n: vec3<f32>, shadowed: f32) -> f32 { return 1.0; }
// @shadow-stub-end
fn key_shadow(wpos: vec3<f32>, n: vec3<f32>) -> f32 {
  if (i32(u.lit.x + 0.5) != 5) { return 1.0; }
  let g = shd_global(wpos, n);
  return shd_light(0, wpos, n, g) * g;
}

fn wrapped(nl: f32) -> f32 { return u.lit.y + (1.0 - u.lit.y) * clamp(nl, 0.0, 1.0); }

// Scene light on a lit surface: diffuse albedo x (1 - metal) x (ambient x ao + key x wrapped N.L + point lights) +
// key specular + environment Fresnel (rough), all in scene units (x u.lit.z by the caller).
fn light_scene(alb: vec3<f32>, n: vec3<f32>, v: vec3<f32>, wpos: vec3<f32>, rough: f32, metal: f32, ao: f32) -> vec3<f32> {
  let l = normalize(u.key_dir.xyz);
  let f0 = mix(vec3<f32>(0.04), alb, metal);
  var local = vec3<f32>(0.0);
  let np = i32(u.npt.x + 0.5);
  for (var k = 0; k < np; k = k + 1) {
    let a = u.pts[2 * k];
    let b = u.pts[2 * k + 1];
    let dv = a.xyz - wpos;
    let d = length(dv);
    local = local + b.rgb * point_falloff(d, a.w, b.w) * wrapped(dot(n, dv / max(d, 1e-4))) * shd_local(a.xyz, wpos, n, 0.0);
  }
  let ks = key_shadow(wpos, n);
  let diffuse = alb * (1.0 - metal) * (u.ambient.rgb * ao + u.key_col.rgb * wrapped(dot(n, l)) * ks + local);
  let spec = spec_ggx(n, v, l, rough, f0) * u.key_col.rgb * u.key_dir.w * ks;
  let nv = max(dot(n, v), 0.0);
  let fr = f0 + (max(vec3<f32>(1.0 - rough), f0) - f0) * pow(1.0 - nv, 5.0);
  return diffuse + spec + fr * u.env_col.rgb * ao;
}

// Tangent frame from screen derivatives (the viewer's packed vertices carry no tangent): T along +u, B along +v.
fn frame_tb(i: VOut, n: vec3<f32>, uv: vec2<f32>) -> mat3x3<f32> {
  let dp1 = dpdx(i.wpos);
  let dp2 = dpdy(i.wpos);
  let du1 = dpdx(uv);
  let du2 = dpdy(uv);
  let p2 = cross(dp2, n);
  let p1 = cross(n, dp1);
  var t = p2 * du1.x + p1 * du2.x;
  var b = p2 * du1.y + p1 * du2.y;
  let m = inverseSqrt(max(max(dot(t, t), dot(b, b)), 1e-20));
  return mat3x3<f32>(t * m, b * m, n);
}

// DXT5nm normal sample (x in alpha, y in green) -> tangent-space xyz (z = sqrt(1 - x^2 - y^2)); xz scaled by `k`
// (the liquid's NormalIntensity scales x and z, as its PS does)
fn nmap_ts(s: vec4<f32>, k: f32) -> vec3<f32> {
  let xy = vec2<f32>(s.a * 2.0 - 1.0, (s.g * 2.0 - 1.0) * u.npt.y);
  let z = sqrt(1.0 - min(dot(xy, xy), 1.0));
  return vec3<f32>(xy.x * k, xy.y, z * k);
}

// The surface normal facing the viewer (both sides lit, as the game's VS flips normals that face away)
fn facing(n: vec3<f32>, v: vec3<f32>) -> vec3<f32> {
  let m = normalize(n + vec3<f32>(1e-6));
  return select(m, -m, dot(m, v) < 0.0);
}

// Lit Uber and Smoke (POPCORN_LIGHTING, notes 10): the Uber colour is the albedo, roughness 1, normal map t10 (a flat
// one when the renderer names none), wrapped diffuse; then the Uber alpha and output rules.
@fragment
fn fs_lit(i: VOut) -> @location(0) vec4<f32> {
  let nm = textureSampleBias(t10, s_rep, i.uv.xy, u.pad0.z);   // game t20: cb10[13].y + cb10[66].w (engine, 0 here)
  var c = uber(i);
  let v = normalize(u.cam_w.xyz);
  let n0 = facing(i.nrm, v);
  let n = normalize(frame_tb(i, n0, i.uv.xy) * nmap_ts(nm, 1.0));
  if (u.f2.w > 0.5 && (i.color.a < 1.0 / 1023.0 || c.a < 1.0 / 1023.0)) { discard; }
  let alb = c.rgb / max(u.feat.w, 1e-6);          // uber() already scaled by the post's colour scale: undo, light, rescale
  c = vec4<f32>(light_scene(alb, n, v, i.wpos, 1.0, 0.0, 1.0) * u.lit.z, c.a);
  c.a = c.a * soft_factor(i.clip);
  let o = finish(c);
  if (o.a <= 0.0 && max(o.r, max(o.g, o.b)) <= 0.0) { discard; }
  return o;
}

// Liquid (Diablo_Liquid, _Mesh, _Ribbon; notes 8): base x mask through the base op, hardness threshold alpha, edge
// colour ramp (or the colour map), dissolve or alpha remap, a normal-mapped GGX surface lit by the scene plus a
// headlight from the camera (display units, Liquid.HeadlightContribution) and a speckle map.
fn liquid(i: VOut) -> vec4<f32> {
  let uv = i.uv.xy;
  // distortion: the UVDistortion.Mask* pair (game VS T8 / T9.y, lane calib5); so_db carries EdgeColor for liquids
  let dd = textureSample(t6, s_rep, uvso(uv, i.so_dm)).rg * 2.0 - vec2<f32>(1.0);
  var uvD = uv;
  if (u.f1.x > 0.5) { uvD = clamp(uv + dd * i.dist.y, vec2<f32>(0.0), vec2<f32>(0.99)); }
  let uvB = uvso(uvD, i.so_base);
  let b = textureSampleBias(t0, s_rep, uvB, u.pad0.x).r;
  let mk = textureSampleBias(t1, s_rep, uvD, u.pad0.x).r;
  let cm_s = textureSample(t3, s_rep, uvso(uv, i.so_mask)).rgb;
  let diss = textureSampleBias(t8, s_rep, uvso(uvD, i.so_x), u.pad0.x).r;
  let nm = textureSampleBias(t10, s_rep, uvB, u.pad0.y);
  let p = b * mk;
  let q = select(p, 1.0 - p, u.lq.z > 0.5);
  let lb = op(i32(u.lq.x + 0.5), p, q);
  // alpha: sat((L - AlphaThreshold) x Hardness^2 x 64 + 0.5)
  var a = clamp((lb - i.so_blend.x) * i.so_blend.y * i.so_blend.y * 64.0 + 0.5, 0.0, 1.0);
  // colour (the game's liquid PS read from a capture by lane calib5, calib5 s3 eid 8331, values matched per stream):
  // e = min(sat(1 - p)^EdgeControl, 1), colour = EdgeColor x mix(e, CenterColor, EdgeHardness); invert: Centre x Edge.
  // (The VS streams T3.z / T3.w are EdgeControl / EdgeHardness, T5 / T6 Edge / Centre: the reverse of the names' order.)
  let e = min(pow(clamp(1.0 - p, 0.0, 1.0), i.so_blend.w), 1.0);
  let centre = i.color.rgb;
  let edge = srgb_to_linear(max(i.so_db.rgb, vec3<f32>(0.0)));
  let inv = u.lq.z > 0.5;
  var rgb = select(edge * mix(vec3<f32>(e), centre, i.so_blend.z), centre * edge, inv);
  let sel = edge;
  if (u.lq2.x > 0.5) {
    var cm = select(cm_s, vec3<f32>(1.0) - cm_s, u.lq.w > 0.5);
    cm = mix(vec3<f32>(e), cm, i.so_blend.z);
    let o2 = i32(u.lq.y + 0.5);
    if (o2 == 0) { rgb = sel * cm; }
    else if (o2 == 1) { rgb = sel + cm; }
    else if (o2 == 2) { rgb = min(sel, max(sel - cm, vec3<f32>(0.0))); }
    else { rgb = sel + sel * cm; }
  }
  // dissolve (AlphaRemap.Dissolve) or alpha remap
  if (u.f1.y > 0.5) {
    if (u.lq2.y > 0.5) {
      let f = clamp(diss - i.cur.x, 0.0, 1.0);
      rgb = clamp(rgb * f, vec3<f32>(0.0), vec3<f32>(1.0));
      a = clamp(a * f, 0.0, 1.0);
    } else {
      a = textureSampleBias(t8, s_clamp, vec2<f32>(a + i.cur.x, i.cur.y), u.pad0.x).r;
    }
  }
  // surface: normal map x NormalIntensity, roughness (1 - p) x Roughness, F0 0.04
  let v = normalize(u.cam_w.xyz);
  let n0 = facing(i.nrm, v);
  let tb = frame_tb(i, n0, uvB);
  let n = normalize(tb * nmap_ts(nm, i.so_alpha.y));
  let rough = clamp((1.0 - p) * i.so_alpha.x, 0.04, 1.0);
  let scene = light_scene(rgb, n, v, i.wpos, rough, 0.0, 1.0) * u.lit.z;
  // headlight: a white light from the camera, display units (added after the game's exposure)
  let nv = max(dot(n, v), 0.0);
  let fh = 0.04 + 0.96 * pow(1.0 - nv, 5.0);
  let hd = rgb * (1.0 - fh) * PI_INV * u.lq2.z * wrapped(nv);
  let hs = spec_ggx(n, v, v, rough, vec3<f32>(0.04)) * u.lq2.w;
  // speckle: the speckle map at normalize(reflect(-cb[2].xyz, n_ts)).xy x p x CubemapIntensity x SpeckleIntensity; cb[2]
  // is the camera's forward axis in world space (calib5 capture: (-0.612, -0.5, -0.612) beside the camera position), so
  // the game reflects the world to-camera vector about the TANGENT-space normal, as written
  let r = normalize(reflect(normalize(u.cam_w.xyz), nmap_ts(nm, i.so_alpha.y)));
  let sp = textureSample(t2, s_rep, r.xy).r * i.so_alpha.z * p * i.so_alpha.w;
  let col = scene + (hd + hs + vec3<f32>(sp)) * u.feat.w;
  return vec4<f32>(col, a * i.color.a);          // colour alpha: 1 x MonsterFade (the VS folds it in)
}

@fragment
fn fs_liquid(i: VOut) -> @location(0) vec4<f32> {
  var c = liquid(i);
  c.a = c.a * soft_factor(i.clip);
  let o = finish(c);
  if (o.a <= 0.0 && max(o.r, max(o.g, o.b)) <= 0.0) { discard; }
  return o;
}

// Diablo_Default_Mesh (popcorn_mesh_p, notes 9) and Diablo_Standard_Mesh drawn with its model's own maps (notes 11).
// Lit: Diffuse.DiffuseMap x colour as albedo, Lit.RoughMetalMap (R occlusion, G roughness, B metal, A 1 - emissive),
// Lit.NormalMap; out = (scene light + emissive) x colour. Unlit: diffuse x colour, Fresnel (gradient by 1 - |N.V|, the
// Fresnel op) and the game's final product with the colour.
@fragment
fn fs_dmesh(i: VOut) -> @location(0) vec4<f32> {
  let uv = uvso(i.uv.xy, i.so_base);
  var alb = textureSampleBias(t0, s_rep, uv, u.pad0.x);
  alb = vec4<f32>(color_variant(alb.rgb, i), alb.a);   // the default mesh PS recolours the diffuse map first
  let orm = textureSample(t2, s_rep, uv);                        // game t12: unbiased
  let nm = textureSampleBias(t10, s_rep, uv, u.pad0.y);
  let v = normalize(u.cam_w.xyz);
  let n0 = facing(i.nrm, v);
  let tb = frame_tb(i, n0, uv);
  let G = textureSampleBias(t4, s_clamp, vec2<f32>(1.0 - abs(dot(normalize(i.nrm + vec3<f32>(1e-6)), v)), i.misc.z), u.pad0.x);
  var c: vec4<f32>;
  let mode = i32(u.lit.x + 0.5);
  if (mode == 3 || mode == 5) {
    let n = normalize(tb * nmap_ts(nm, 1.0));
    let lit = light_scene(alb.rgb, n, v, i.wpos, clamp(orm.g, 0.04, 1.0), orm.b, orm.r);
    let emis = select(vec3<f32>(0.0), alb.rgb * (1.0 - orm.a), mode == 3);   // a model's ORM A is its specular level
    c = vec4<f32>((lit + emis) * i.color.rgb * u.lit.z * u.tint.rgb, alb.a * i.color.a * u.tint.a);
  } else {
    let base = alb * i.color * u.tint;
    var sel = base;
    if (u.f0.w > 0.5) {
      let r = fop(i32(u.fres.x + 0.5), base, G);
      sel = vec4<f32>(select(base.rgb, r.rgb, u.fres.y > 0.5), select(base.a, r.a, u.fres.z > 0.5));
      sel = sel * base;
    }
    c = vec4<f32>(sel.rgb * i.misc.y * u.feat.w, sel.a);
  }
  if (u.lit.w > 0.5) {                                  // m 4: opaque (the game's G-buffer pass): no blending
    if (c.a < 0.001) { discard; }
    return vec4<f32>(c.rgb, 1.0);
  }
  c.a = c.a * soft_factor(i.clip);
  let o = finish(c);
  if (o.a <= 0.0 && max(o.r, max(o.g, o.b)) <= 0.0) { discard; }
  return o;
}

// Diablo_Stencil (popcorn_stencil_p, notes 12): no colour; the AlphaThreshold mask (optionally inverted, then through
// the remap) decides where the stencil is written. Viewer: those pixels get the far depth, so the effect's draws that
// follow are seen "through" the ground there (GUESS for what the game's stencil test does with it).
struct StencilOut {
  @location(0) c: vec4<f32>,
  @builtin(frag_depth) depth: f32,
};

@fragment
fn fs_stencil(i: VOut) -> StencilOut {
  var m = textureSampleBias(t0, s_rep, uvso(i.uv.xy, i.so_base), u.pad0.x).r;
  if (u.lq.z > 0.5) { m = 1.0 - m; }
  var a = m;
  if (u.f1.y > 0.5) { a = textureSampleBias(t8, s_clamp, vec2<f32>(m + i.cur.x, i.cur.y), u.pad0.x - u.pad0.z).r; }   // game t11: cb4[6].y alone
  if (a < 0.001) { discard; }
  var o: StencilOut;
  o.c = vec4<f32>(0.0);
  o.depth = 1.0;
  return o;
}

// WaterMaterial (the game's water_p, ps_water_p.bin; lane r4-water, constants checked on capture r3-calibfix r4 eid 19928).
// Uniforms (render.water_material): dec[0] ShallowAlbedo (linear) + ShallowAlpha, dec[1] DeepAlbedo + DeepAlpha,
// dec[2] FoamAlbedo + ShoreFoamDepth, dec[3] (WaterTransmissionDistanceMax, Roughness, WaterFlowNormalIntensity,
// WaterFlowNormalDetailIntensity), xv.yzw the uv scales. Textures: t10 NormalTexture, t1 DetailNormalTexture, t3
// FoamMaskTexture. The game: thickness = the scene depth behind - the water's depth (view axis); colour and alpha =
// lerp(shallow, deep, sat(thickness / TransmissionMax)); shore foam e = 1 - sat(thickness / ShoreFoamDepth),
// f = e^2 x lerp(mask, 1, e^12), albedo = lerp(albedo, foam, f); the normal = flow map + detail map (partial
// derivative blend); lit with GGX + ambient + environment; out = lerp(scene colour at the same pixel, lit, a),
// a = F + (1 - F) x alpha, F = 0.04 + (max(1 - rough, 0.04) - 0.04) (1 - N.V)^5. The scene sample has no normal offset,
// so the "refraction" is this blend over what is behind (here: premultiplied over the scene and the --bg plate).
// The flow (lane r4-glow): two phases half a cycle apart, ph = t x speed + 0.5 snoise(xz / 10) (u.ops.z, the PS's 65 x the
// simplex sum), each moves the uv back along the flow vector u.ops.xy; phase A at the flow scale, B at the detail scale
// + 0.5; at each, both normal maps; A and B blended by |1 - 2 fract(ph + 0.5)|. The foam mask scrolls by u.fres.xy.
fn water_n(uv: vec2<f32>) -> vec3<f32> {
  let nb0 = nmap_ts(textureSampleBias(t10, s_rep, uv, u.pad0.y), 1.0);
  let nd0 = nmap_ts(textureSampleBias(t1, s_rep, uv, u.pad0.y), 1.0);
  return normalize(vec3<f32>(nb0.xy * u.dec[3].z, nb0.z)) + normalize(vec3<f32>(nd0.xy * u.dec[3].w, nd0.z));
}

fn perm289(x: vec3<f32>) -> vec3<f32> {
  let y = (x * 34.0 + 1.0) * x;
  return y - floor(y * (1.0 / 289.0)) * 289.0;
}

// 2D simplex noise (Ashima / Gustavson, MIT), as ps_water_p inlines it (without the final x 130)
fn snoise_sum(v: vec2<f32>) -> f32 {
  let i0 = floor(v + dot(v, vec2<f32>(0.366025403)));
  let x0 = v - i0 + dot(i0, vec2<f32>(0.211324865));
  let o = select(vec2<f32>(1.0, 0.0), vec2<f32>(0.0, 1.0), x0.y > x0.x);
  let x1 = x0 + 0.211324865 - o;
  let x2 = x0 - 0.577350269;
  let i = i0 - floor(i0 * (1.0 / 289.0)) * 289.0;
  let p = perm289(perm289(i.y + vec3<f32>(0.0, o.y, 1.0)) + i.x + vec3<f32>(0.0, o.x, 1.0));
  var m = max(vec3<f32>(0.5) - vec3<f32>(dot(x0, x0), dot(x1, x1), dot(x2, x2)), vec3<f32>(0.0));
  m = m * m;
  m = m * m;
  let x = 2.0 * fract(p * (1.0 / 41.0)) - 1.0;
  let h = abs(x) - 0.5;
  let a0 = x - floor(x + 0.5);
  m = m * (1.79284291 - 0.85373472 * (a0 * a0 + h * h));
  return dot(m, vec3<f32>(a0.x * x0.x + h.x * x0.y, a0.y * x1.x + h.y * x1.y, a0.z * x2.x + h.z * x2.y));
}

@fragment
fn fs_water(i: VOut) -> @location(0) vec4<f32> {
  let zs = textureLoad(t_depth, vec2<i32>(i.clip.xy), 0).r;
  let th = select((zs - i.clip.z) * u.cam_w.w, 1e4, zs >= 0.99999);    // nothing behind: deep water
  let k = clamp(th / max(u.dec[3].x, 1e-4), 0.0, 1.0);
  var w = mix(u.dec[0], u.dec[1], k);
  let e = 1.0 - clamp(th / max(u.dec[2].w, 1e-4), 0.0, 1.0);
  let mask = textureSample(t3, s_rep, (i.wpos.xz + u.fres.xy) * u.xv.w).r;
  let foam = e * e * mix(mask, 1.0, pow(e, 12.0));
  let alb = mix(w.rgb, u.dec[2].rgb, foam);
  let ph = snoise_sum(i.wpos.xz * 0.1) * 65.0 + u.ops.z;
  let p1 = fract(ph + 0.5);
  var flow = u.ops.xy;
  if (u.ops.w > 0.5) {
    // ps_water_p t13 / s3: point clamp the original mesh UV at mip zero.
    let dims = vec2<i32>(textureDimensions(t6));
    let texel = clamp(vec2<i32>(i.uv.xy * vec2<f32>(dims)), vec2<i32>(0), dims - 1);
    let rg = textureLoad(t6, texel, 0).rg * 2.0 - 1.0;
    flow = rg.x * u.ops.xy + rg.y * vec2<f32>(-u.ops.y, u.ops.x);
  }
  let sa = water_n((i.wpos.xz - flow * p1) * u.xv.y);
  let sb = water_n((i.wpos.xz - flow * fract(ph) + 0.5) * u.xv.z);
  let ta = sa.xy / max(sa.z, 5.96e-8);
  let ts = normalize(vec3<f32>(ta + abs(1.0 - 2.0 * p1) * (sb.xy / max(sb.z, 5.96e-8) - ta), 1.0));
  let uvb = i.wpos.xz * u.xv.y;
  let v = normalize(u.cam_w.xyz);
  let n0 = facing(i.nrm, v);
  let n = normalize(frame_tb(i, n0, uvb) * ts);
  let rough = u.dec[3].y;
  let lit = light_scene(alb, n, v, i.wpos, rough, 0.0, 1.0) * u.lit.z;
  let f = 0.04 + (max(1.0 - rough, 0.04) - 0.04) * pow(1.0 - clamp(dot(n, v), 0.0, 1.0), 5.0);
  let a = clamp(f + (1.0 - f) * w.a, 0.0, 1.0) * i.color.a;
  return vec4<f32>(lit * a, a);
}
